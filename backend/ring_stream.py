import discord
from discord.ext import commands
import asyncio
import threading
import queue as sync_queue

class AsyncQueueAudioSource(discord.AudioSource):
    """
    Input:
        48 kHz, signed 16-bit little-endian mono PCM.

    Output to Discord:
        48 kHz, signed 16-bit little-endian stereo PCM.
        Exactly 20 ms / 3,840 bytes per read().
    """

    SAMPLES_PER_FRAME = 960
    SAMPLE_WIDTH = 2
    OUTPUT_CHANNELS = 2

    def __init__(
        self,
        audio_queue: asyncio.Queue,
        discord_loop: asyncio.AbstractEventLoop,
        input_channels: int = 1,
        prebuffer_frames: int = 3,
    ):
        self.audio_queue = audio_queue
        self.discord_loop = discord_loop
        self.input_channels = input_channels

        if input_channels not in (1, 2):
            raise ValueError("input_channels must be 1 or 2")

        self.input_frame_bytes = (
            self.SAMPLES_PER_FRAME
            * self.SAMPLE_WIDTH
            * self.input_channels
        )
        self.output_frame_bytes = (
            self.SAMPLES_PER_FRAME
            * self.SAMPLE_WIDTH
            * self.OUTPUT_CHANNELS
        )

        self.prebuffer_bytes = self.input_frame_bytes * prebuffer_frames

        self._sync_queue: sync_queue.Queue[bytes | None] = sync_queue.Queue()
        self._buffer = bytearray()

        self._done = threading.Event()
        self._started = False

        self._bridge_task = asyncio.run_coroutine_threadsafe(
            self._bridge(),
            self.discord_loop,
        )

    async def _bridge(self) -> None:
        try:
            while True:
                data = await self.audio_queue.get()

                if data is None:
                    self._sync_queue.put(None)
                    return

                if data:
                    self._sync_queue.put(bytes(data))
        finally:
            self._done.set()

    def _receive_chunk(self, timeout: float) -> bool:
        """
        Receive one chunk.

        Returns False when the producer has finished.
        """
        try:
            chunk = self._sync_queue.get(timeout=timeout)
        except sync_queue.Empty:
            return True

        if chunk is None:
            self._done.set()
            return False

        self._buffer.extend(chunk)
        return True

    @staticmethod
    def _mono_to_stereo(data: bytes) -> bytes:
        """
        Duplicate every signed 16-bit mono sample into left and right.
        """
        output = bytearray(len(data) * 2)

        source = memoryview(data)
        destination = memoryview(output)

        # Each mono sample occupies two bytes.
        destination[0::4] = source[0::2]
        destination[1::4] = source[1::2]
        destination[2::4] = source[0::2]
        destination[3::4] = source[1::2]

        return bytes(output)

    def read(self) -> bytes:
        # Build an initial jitter buffer before playback begins.
        target_bytes = (
            self.prebuffer_bytes
            if not self._started
            else self.input_frame_bytes
        )

        while len(self._buffer) < target_bytes and not self._done.is_set():
            # Waiting here is better than inserting a silence frame whenever
            # the asyncio producer is a few milliseconds late.
            if not self._receive_chunk(timeout=0.02):
                break

        if not self._started and len(self._buffer) >= self.input_frame_bytes:
            self._started = True

        if len(self._buffer) < self.input_frame_bytes:
            if self._done.is_set():
                if not self._buffer:
                    # Empty bytes tells discord.py playback is complete.
                    return b""

                # Pad only the final incomplete frame.
                missing = self.input_frame_bytes - len(self._buffer)
                self._buffer.extend(b"\x00" * missing)
            else:
                # Genuine underrun. Returning silence keeps the player alive,
                # but this should be rare because of the jitter buffer.
                return b"\x00" * self.output_frame_bytes

        frame = bytes(self._buffer[:self.input_frame_bytes])
        del self._buffer[:self.input_frame_bytes]

        if self.input_channels == 1:
            return self._mono_to_stereo(frame)

        return frame

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        self._done.set()

        if self._bridge_task and not self._bridge_task.done():
            self._bridge_task.cancel()

    async def stop(self) -> None:
        await self.audio_queue.put(None)

        try:
            await asyncio.wrap_future(self._bridge_task)
        except asyncio.CancelledError:
            pass
            
class FFmpegAudioSource(discord.AudioSource):
    def __init__(self, ffmpeg_proc: asyncio.subprocess.Process):
        self.proc = ffmpeg_proc

    def read(self) -> bytes:
        try:
            return self.proc.stdout.read(3840)
        except Exception:
            return b""

    def is_opus(self) -> bool:
        return False


class voiceStream:
    @staticmethod
    async def connect(token: str, channel_id: int | None = None):
        bot_element = commands.Bot(
            command_prefix="ain't no way someone is guessing that bro",
            self_bot=True,
        )

        voice_client = None
        ready_event = asyncio.Event()
        future = asyncio.get_event_loop().create_future()

        @bot_element.event
        async def on_ready():
            nonlocal voice_client
            try:
                if channel_id is not None:
                    voice_channel = await bot_element.fetch_channel(channel_id)
                    voice_client = await voice_channel.connect()
                    print(f"{bot_element.user} is calling")
                else:
                    print(f"{bot_element.user} is ready")
                    
                future.set_result(voice_client)
            except Exception as e:
                print("Error in on_ready:", e)
                return future.set_result(400)
            finally:
                ready_event.set()

        asyncio.create_task(bot_element.start(token))

        await ready_event.wait()
        
        future_response = await future
        
        if future_response == 400:
        
            await bot_element.close()

        return future_response