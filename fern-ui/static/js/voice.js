import { api } from "./api.js";

const TARGET_SAMPLE_RATE = 48000;
const PROCESSOR_BUFFER_SIZE = 1024;
const MAX_BUFFERED_AMOUNT = 256 * 1024;

let talkSocket = null;
let listenSocket = null;
let captureContext = null;
let playbackContext = null;
let mediaStream = null;
let sourceNode = null;
let processorNode = null;
let silentGainNode = null;
let listenCryptoKey = null;
let nextPlaybackTime = 0;
let talking = false;
let listening = false;
let callEstablished = false;
let muted = false;
let deafened = false;
let sentChunks = 0;
let receivedChunks = 0;
let droppedChunks = 0;
let callStartTime = 0;
let callDurationTimer = null;
let currentCallChannelId = null;
let currentCallTitle = "";

function getWsUrl(path) {
  const base = (window.FERN_BACKEND_URL || "http://45.154.98.198:5001").replace(/^http/, "ws");
  const lic = window.FERN_LICENCE || "ADMIN";
  return `${base}/call/${path}/discord/${encodeURIComponent(lic)}`;
}

function waitForSocketOpen(webSocket, timeoutMs = 10000) {
  return new Promise((resolve, reject) => {
    if (webSocket.readyState === WebSocket.OPEN) {
      resolve();
      return;
    }
    let settled = false;
    const timeoutId = window.setTimeout(() => {
      finish(reject, new Error("WebSocket connection timed out."));
    }, timeoutMs);

    function cleanup() {
      window.clearTimeout(timeoutId);
      webSocket.removeEventListener("open", handleOpen);
      webSocket.removeEventListener("error", handleError);
      webSocket.removeEventListener("close", handleClose);
    }

    function finish(callback, value) {
      if (settled) return;
      settled = true;
      cleanup();
      callback(value);
    }

    function handleOpen() { finish(resolve); }
    function handleError() { finish(reject, new Error("WebSocket connection failed.")); }
    function handleClose(event) { finish(reject, new Error(`WebSocket closed before opening. Code: ${event.code}`)); }

    webSocket.addEventListener("open", handleOpen);
    webSocket.addEventListener("error", handleError);
    webSocket.addEventListener("close", handleClose);
  });
}

function waitForListenKey(webSocket, timeoutMs = 10000) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const timeoutId = window.setTimeout(() => {
      finish(reject, new Error("Timed out waiting for audio key."));
    }, timeoutMs);

    function cleanup() {
      window.clearTimeout(timeoutId);
      webSocket.removeEventListener("message", handleMessage);
      webSocket.removeEventListener("close", handleClose);
    }

    function finish(callback, value) {
      if (settled) return;
      settled = true;
      cleanup();
      callback(value);
    }

    async function handleMessage(event) {
      if (typeof event.data !== "string") return;
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch {
        return;
      }
      if (payload.type === "audio_key" && payload.key) {
        try {
          finish(resolve, await importAudioKey(payload.key));
        } catch (error) {
          finish(reject, error);
        }
      } else if (payload.error) {
        finish(reject, new Error(payload.error));
      }
    }

    function handleClose(event) {
      finish(reject, new Error(`WebSocket closed before key. Code: ${event.code}`));
    }

    webSocket.addEventListener("message", handleMessage);
    webSocket.addEventListener("close", handleClose);
  });
}

async function importAudioKey(keyString) {
  const encodedKey = new TextEncoder().encode(keyString);
  const hashedKey = await crypto.subtle.digest("SHA-256", encodedKey);
  return crypto.subtle.importKey(
    "raw",
    hashedKey,
    { name: "AES-GCM" },
    false,
    ["decrypt"]
  );
}

async function decryptAudioChunk(encryptedBuffer) {
  const encrypted = new Uint8Array(encryptedBuffer);
  if (encrypted.length < 13) {
    throw new Error("Encrypted audio chunk is too short.");
  }
  const iv = encrypted.slice(0, 12);
  const ciphertext = encrypted.slice(12);
  return crypto.subtle.decrypt(
    { name: "AES-GCM", iv },
    listenCryptoKey,
    ciphertext
  );
}

function float32ToPcm16(input) {
  const outputBuffer = new ArrayBuffer(input.length * 2);
  const view = new DataView(outputBuffer);
  for (let index = 0; index < input.length; index++) {
    const sample = Math.max(-1, Math.min(1, input[index]));
    const pcmSample = sample < 0 ? Math.round(sample * 32768) : Math.round(sample * 32767);
    view.setInt16(index * 2, pcmSample, true);
  }
  return outputBuffer;
}

function pcm16ToFloat32(pcmBuffer) {
  const view = new DataView(pcmBuffer);
  const samples = new Float32Array(pcmBuffer.byteLength / 2);
  for (let index = 0; index < samples.length; index++) {
    const sample = view.getInt16(index * 2, true);
    samples[index] = sample < 0 ? sample / 32768 : sample / 32767;
  }
  return samples;
}

function playPcm16(pcmBuffer) {
  if (!playbackContext || deafened) return;
  const samples = pcm16ToFloat32(pcmBuffer);
  const audioBuffer = playbackContext.createBuffer(1, samples.length, TARGET_SAMPLE_RATE);
  audioBuffer.copyToChannel(samples, 0);

  const node = playbackContext.createBufferSource();
  node.buffer = audioBuffer;
  node.connect(playbackContext.destination);

  const startAt = Math.max(playbackContext.currentTime + 0.02, nextPlaybackTime);
  node.start(startAt);
  nextPlaybackTime = startAt + audioBuffer.duration;
}

function handleAudioProcess(event) {
  if (!talking || muted || !talkSocket || talkSocket.readyState !== WebSocket.OPEN) {
    return;
  }
  if (talkSocket.bufferedAmount > MAX_BUFFERED_AMOUNT) {
    droppedChunks++;
    return;
  }
  const input = event.inputBuffer.getChannelData(0);
  const pcmBuffer = float32ToPcm16(input);
  talkSocket.send(pcmBuffer);
  sentChunks++;
}

async function startTalking() {
  if (talking) return;
  try {
    const streamUrl = getWsUrl("stream");
    talkSocket = new WebSocket(streamUrl);
    talkSocket.binaryType = "arraybuffer";

    talkSocket.addEventListener("close", (event) => {
      console.log("talkSocket closed:", event.code, event.reason);
      talking = false;
      updateVoiceUI();
    });

    talkSocket.addEventListener("message", (event) => {
      if (typeof event.data === "string") {
        try {
          const payload = JSON.parse(event.data);
          if (payload.type === "call_ended" || payload.event === "call_ended") {
            endVoiceCall();
          }
        } catch {}
      }
    });

    await waitForSocketOpen(talkSocket).catch(() => {});

    try {
      captureContext = new (window.AudioContext || window.webkitAudioContext)({
        sampleRate: TARGET_SAMPLE_RATE,
        latencyHint: "interactive"
      });

      if (captureContext.state === "suspended") {
        await captureContext.resume();
      }
    } catch {}

    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true
        },
        video: false
      });
    } catch {
      try {
        mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
      } catch (micErr) {
        console.warn("Microphone not available:", micErr);
        mediaStream = null;
        muted = true;
      }
    }

    if (mediaStream && captureContext) {
      try {
        sourceNode = captureContext.createMediaStreamSource(mediaStream);
        processorNode = captureContext.createScriptProcessor(PROCESSOR_BUFFER_SIZE, 1, 1);
        processorNode.onaudioprocess = handleAudioProcess;

        silentGainNode = captureContext.createGain();
        silentGainNode.gain.value = 0;

        sourceNode.connect(processorNode);
        processorNode.connect(silentGainNode);
        silentGainNode.connect(captureContext.destination);

        talking = true;
      } catch {}
    }

    updateVoiceUI();
  } catch (error) {
    console.warn("startTalking non-fatal error:", error);
    updateVoiceUI();
  }
}

async function startListening() {
  if (listening) return;
  try {
    try {
      playbackContext = new (window.AudioContext || window.webkitAudioContext)({
        sampleRate: TARGET_SAMPLE_RATE,
        latencyHint: "interactive"
      });

      if (playbackContext.state === "suspended") {
        await playbackContext.resume();
      }
      nextPlaybackTime = playbackContext.currentTime + 0.05;
    } catch {}

    const listenUrl = getWsUrl("listen");
    listenSocket = new WebSocket(listenUrl);
    listenSocket.binaryType = "arraybuffer";

    listenSocket.addEventListener("close", (event) => {
      console.log("listenSocket closed:", event.code, event.reason);
      listening = false;
      updateVoiceUI();
    });

    listenSocket.addEventListener("message", async (event) => {
      if (typeof event.data === "string") {
        try {
          const payload = JSON.parse(event.data);
          if (payload.type === "call_ended" || payload.event === "call_ended") {
            endVoiceCall();
            return;
          }
          if (payload.type === "audio_key" && payload.key) {
            listenCryptoKey = await importAudioKey(payload.key);
          }
        } catch {}
        return;
      }
      if (!listenCryptoKey || deafened) {
        droppedChunks++;
        return;
      }
      try {
        const pcmBuffer = await decryptAudioChunk(event.data);
        receivedChunks++;
        playPcm16(pcmBuffer);
      } catch (error) {
        droppedChunks++;
      }
    });

    await waitForSocketOpen(listenSocket).catch(() => {});
    try {
      listenCryptoKey = await waitForListenKey(listenSocket, 4000);
    } catch {}

    listening = true;
    updateVoiceUI();
  } catch (error) {
    console.warn("startListening non-fatal error:", error);
    updateVoiceUI();
  }
}

async function stopTalking(sendStopMessage = false) {
  talking = false;
  if (processorNode) {
    processorNode.onaudioprocess = null;
    try { processorNode.disconnect(); } catch {}
    processorNode = null;
  }
  if (sourceNode) {
    try { sourceNode.disconnect(); } catch {}
    sourceNode = null;
  }
  if (silentGainNode) {
    try { silentGainNode.disconnect(); } catch {}
    silentGainNode = null;
  }
  if (mediaStream) {
    for (const track of mediaStream.getTracks()) track.stop();
    mediaStream = null;
  }
  if (captureContext) {
    try { await captureContext.close(); } catch {}
    captureContext = null;
  }
  if (talkSocket) {
    if (sendStopMessage && talkSocket.readyState === WebSocket.OPEN) {
      talkSocket.send(JSON.stringify({ type: "stop" }));
    }
    if (talkSocket.readyState === WebSocket.OPEN || talkSocket.readyState === WebSocket.CONNECTING) {
      talkSocket.close(1000, "Stopped talking");
    }
    talkSocket = null;
  }
  updateVoiceUI();
}

async function stopListening() {
  listening = false;
  listenCryptoKey = null;
  nextPlaybackTime = 0;
  if (listenSocket) {
    if (listenSocket.readyState === WebSocket.OPEN || listenSocket.readyState === WebSocket.CONNECTING) {
      listenSocket.close(1000, "Stopped listening");
    }
    listenSocket = null;
  }
  if (playbackContext) {
    try { await playbackContext.close(); } catch {}
    playbackContext = null;
  }
  updateVoiceUI();
}

export async function endVoiceCall() {
  removeVoiceUI();
  callEstablished = false;
  currentCallChannelId = null;
  currentCallTitle = "";
  if (callDurationTimer) {
    clearInterval(callDurationTimer);
    callDurationTimer = null;
  }
  try {
    fetch("/api/leave-call", { method: "POST" }).catch(() => {});
  } catch {}
  await stopTalking(true);
  await stopListening();
  removeVoiceUI();
}

export function toggleMute() {
  muted = !muted;
  if (mediaStream) {
    mediaStream.getAudioTracks().forEach(track => {
      track.enabled = !muted;
    });
  }
  updateVoiceUI();
}

export function toggleDeafen() {
  deafened = !deafened;
  if (deafened) {
    muted = true;
  }
  if (mediaStream) {
    mediaStream.getAudioTracks().forEach(track => {
      track.enabled = !muted;
    });
  }
  if (playbackContext) {
    if (deafened && playbackContext.state === "running") {
      playbackContext.suspend().catch(() => {});
    } else if (!deafened && playbackContext.state === "suspended") {
      playbackContext.resume().catch(() => {});
    }
  }
  updateVoiceUI();
}

export async function joinVoiceCall(channelId, title = "Voice Channel") {
  if (currentCallChannelId === channelId) return;
  if (currentCallChannelId) {
    await endVoiceCall();
  }

  currentCallChannelId = channelId;
  currentCallTitle = title;
  callEstablished = false;
  callStartTime = Date.now();
  sentChunks = 0;
  receivedChunks = 0;
  droppedChunks = 0;
  muted = false;
  deafened = false;

  renderVoiceUI();
  if (callDurationTimer) clearInterval(callDurationTimer);
  callDurationTimer = setInterval(updateCallDuration, 1000);

  try {
    await api.ringPrivateCall(channelId);
  } catch (err) {
    console.error("Ring call signaling error:", err);
  }

  startListening().catch(console.error);
  startTalking().catch(console.error);
}

function updateCallDuration() {
  const el = document.getElementById("voice-call-time");
  if (!el || !callStartTime) return;
  const sec = Math.floor((Date.now() - callStartTime) / 1000);
  const m = Math.floor(sec / 60);
  const s = String(sec % 60).padStart(2, "0");
  el.textContent = `${m}:${s}`;
}

function renderVoiceUI() {
  removeVoiceUI();
  const panel = document.createElement("div");
  panel.id = "fern-voice-panel";
  panel.className = "voice-panel";
  panel.innerHTML = `
    <div class="voice-panel-status">
      <div class="voice-panel-signal">
        <i class="fa-solid fa-phone"></i>
        <span class="voice-status-dot"></span>
      </div>
      <div class="voice-panel-info">
        <div class="voice-channel-name">${currentCallTitle || "Voice Connected"}</div>
        <div class="voice-status-sub">
          <span id="voice-call-time">0:00</span> · <span class="voice-secure-tag"><i class="fa-solid fa-lock"></i> Encrypted (AES-GCM)</span>
        </div>
      </div>
    </div>
    <div class="voice-panel-actions">
      <button type="button" class="voice-btn" id="voice-mute-btn" title="Mute Microphone">
        <i class="fa-solid fa-microphone"></i>
      </button>
      <button type="button" class="voice-btn" id="voice-deafen-btn" title="Deafen Audio">
        <i class="fa-solid fa-headphones"></i>
      </button>
      <button type="button" class="voice-btn danger" id="voice-disconnect-btn" title="Disconnect">
        <i class="fa-solid fa-phone-slash"></i>
      </button>
    </div>
  `;

  document.body.appendChild(panel);

  const muteBtn = panel.querySelector("#voice-mute-btn");
  const deafenBtn = panel.querySelector("#voice-deafen-btn");
  const disconnectBtn = panel.querySelector("#voice-disconnect-btn");

  if (muteBtn) {
    muteBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      toggleMute();
    });
  }
  if (deafenBtn) {
    deafenBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      toggleDeafen();
    });
  }
  if (disconnectBtn) {
    disconnectBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      endVoiceCall();
    });
  }

  panel.addEventListener("click", (e) => {
    const btn = e.target.closest(".voice-btn");
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    if (btn.id === "voice-mute-btn") {
      toggleMute();
    } else if (btn.id === "voice-deafen-btn") {
      toggleDeafen();
    } else if (btn.id === "voice-disconnect-btn") {
      endVoiceCall();
    }
  });

  updateVoiceUI();
}

function updateVoiceUI() {
  if (!currentCallChannelId) {
    removeVoiceUI();
    return;
  }
  const panel = document.getElementById("fern-voice-panel");
  if (!panel) return;

  const muteBtn = panel.querySelector("#voice-mute-btn");
  if (muteBtn) {
    muteBtn.className = `voice-btn ${muted ? "active-off" : "active-on"}`;
    muteBtn.innerHTML = `<i class="fa-solid ${muted ? "fa-microphone-slash" : "fa-microphone"}"></i>`;
  }

  const deafenBtn = panel.querySelector("#voice-deafen-btn");
  if (deafenBtn) {
    deafenBtn.className = `voice-btn ${deafened ? "active-off" : "active-on"}`;
    deafenBtn.innerHTML = `<i class="fa-solid ${deafened ? "fa-headphones-simple" : "fa-headphones"}"></i>`;
  }

  const statusDot = panel.querySelector(".voice-status-dot");
  if (statusDot) {
    statusDot.className = `voice-status-dot ${listening || talking ? "connected" : "connecting"}`;
  }
}

function removeVoiceUI() {
  const panel = document.getElementById("fern-voice-panel");
  if (panel) panel.remove();
  document.querySelectorAll(".voice-panel, #fern-voice-panel").forEach(el => el.remove());
}

export function isCallActive() {
  return Boolean(currentCallChannelId);
}

export function getCurrentCallId() {
  return currentCallChannelId;
}

if (typeof window !== "undefined") {
  window.FernVoice = {
    joinVoiceCall,
    endVoiceCall,
    toggleMute,
    toggleDeafen,
    isCallActive,
    getCurrentCallId,
  };
  window.joinVoiceCall = joinVoiceCall;
  window.endVoiceCall = endVoiceCall;
}
