var licenseValid = false;
var validateTimer = null;

var licKeyInput = document.querySelector('input[name="license_key"]');
var statusEl = document.getElementById('license-status');
var formEl = document.querySelector('form');
var submitBtn = document.getElementById('submit-btn');

var qrSessionId = null;
var qrPollTimer = null;

var qrImage = document.getElementById('qr-image');
var qrLoading = document.getElementById('qr-loading');
var qrScanned = document.getElementById('qr-scanned');
var qrAvatar = document.getElementById('qr-avatar');
var qrUsername = document.getElementById('qr-username');
var qrStatus = document.getElementById('qr-status');
var qrBox = document.getElementById('qr-box');

function generateQR() {
  if (qrPollTimer) {
    clearInterval(qrPollTimer);
    qrPollTimer = null;
  }
  qrSessionId = null;
  if (qrLoading) qrLoading.style.display = 'flex';
  if (qrImage) {
    qrImage.style.display = 'none';
    qrImage.src = '';
  }
  if (qrScanned) qrScanned.style.display = 'none';
  if (qrStatus) {
    qrStatus.className = 'qr-status-text';
    qrStatus.textContent = 'Generating QR code...';
  }

  fetch('/api/qr-login/generate')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.session_id) {
        qrSessionId = data.session_id;
        startQRPoll(qrSessionId);
      } else {
        if (qrStatus) qrStatus.textContent = 'Failed to generate QR code.';
      }
    })
    .catch(function() {
      if (qrStatus) qrStatus.textContent = 'Error connecting to server.';
    });
}

function startQRPoll(sid) {
  if (qrPollTimer) clearInterval(qrPollTimer);
  var imgLoaded = false;

  qrPollTimer = setInterval(function() {
    fetch('/api/qr-login/status/' + sid)
      .then(function(r) { return r.json(); })
      .then(function(d) {
        if (d.status === 'pending') {
          if (!imgLoaded) {
            imgLoaded = true;
            if (qrImage) {
              qrImage.src = '/api/qr-login/qr/' + sid;
              qrImage.onload = function() {
                if (qrLoading) qrLoading.style.display = 'none';
                qrImage.style.display = 'block';
              };
            }
            if (qrStatus) qrStatus.textContent = 'Scan with Discord mobile app';
          }
        } else if (d.status === 'scanned') {
          if (qrImage) qrImage.style.display = 'none';
          if (qrLoading) qrLoading.style.display = 'none';
          if (qrScanned) qrScanned.style.display = 'flex';
          if (qrAvatar && d.avatar_url) qrAvatar.src = d.avatar_url;
          if (qrUsername) qrUsername.textContent = d.username || 'Discord User';
          if (qrStatus) {
            qrStatus.className = 'qr-status-text highlight';
            qrStatus.textContent = 'Confirm login on your phone...';
          }
        } else if (d.status === 'confirmed') {
          clearInterval(qrPollTimer);
          qrPollTimer = null;
          if (qrStatus) {
            qrStatus.className = 'qr-status-text success';
            qrStatus.innerHTML = '<i class="fa-solid fa-check"></i> Confirmed! Logging in...';
          }
          var nextInput = document.querySelector('input[name="next"]');
          var nextVal = nextInput ? nextInput.value : '';
          var redirectUrl = '/login?confirm_qr=' + sid;
          if (nextVal) redirectUrl += '&next=' + encodeURIComponent(nextVal);
          window.location.href = redirectUrl;
        } else if (d.status === 'expired') {
          clearInterval(qrPollTimer);
          qrPollTimer = null;
          if (qrStatus) qrStatus.textContent = 'QR Code expired. Refreshing...';
          setTimeout(generateQR, 1200);
        } else if (d.status === 'error') {
          clearInterval(qrPollTimer);
          qrPollTimer = null;
          if (qrStatus) {
            qrStatus.className = 'qr-status-text error';
            qrStatus.textContent = d.error || 'Login cancelled or failed.';
          }
          setTimeout(generateQR, 2500);
        }
      })
      .catch(function() {});
  }, 1500);
}

if (qrBox) {
  qrBox.addEventListener('click', function() {
    if (qrStatus && (qrStatus.classList.contains('error') || qrStatus.textContent.indexOf('expired') !== -1)) {
      generateQR();
    }
  });
}

function setLicenseState(state) {
  if (!statusEl) return;
  statusEl.className = 'input-status';
  statusEl.innerHTML = '';
  
  if (state === 'valid') {
    licenseValid = true;
    statusEl.className += ' valid';
    statusEl.innerHTML = '<i class="fa-solid fa-check"></i>';
    if (licKeyInput) licKeyInput.classList.remove('is-invalid');
  } else if (state === 'invalid') {
    licenseValid = false;
    statusEl.className += ' invalid';
    statusEl.innerHTML = '<i class="fa-solid fa-xmark"></i>';
    if (licKeyInput) licKeyInput.classList.add('is-invalid');
  } else if (state === 'checking') {
    licenseValid = false;
    statusEl.className += ' spinner';
    statusEl.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i>';
    if (licKeyInput) licKeyInput.classList.remove('is-invalid');
  } else {
    licenseValid = true;
    if (licKeyInput) licKeyInput.classList.remove('is-invalid');
  }
}

function validateLicense() {
  var v = (licKeyInput ? licKeyInput.value : '').trim();
  if (!v) { setLicenseState('empty'); return; }
  setLicenseState('checking');
  fetch('/api/validate-license', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ license_key: v })
  }).then(function(r) { return r.json(); }).then(function(d) {
    if (d.valid) { setLicenseState('valid'); }
    else { setLicenseState('invalid'); }
  }).catch(function() {
    setLicenseState('invalid');
  });
}

if (licKeyInput) {
  licKeyInput.addEventListener('input', function() {
    if (validateTimer) clearTimeout(validateTimer);
    var v = this.value.trim();
    if (!v) { setLicenseState('empty'); return; }
    setLicenseState('checking');
    validateTimer = setTimeout(validateLicense, 350);
  });
}

var tokenInput = document.querySelector('input[name="bot_token"]');
var toggleTokenBtn = document.getElementById('toggle-token-btn');
var tokenEyeIcon = document.getElementById('token-eye-icon');
if (toggleTokenBtn && tokenInput && tokenEyeIcon) {
  toggleTokenBtn.addEventListener('click', function(e) {
    e.preventDefault();
    if (tokenInput.type === 'password') {
      tokenInput.type = 'text';
      tokenEyeIcon.className = 'fa-regular fa-eye-slash';
    } else {
      tokenInput.type = 'password';
      tokenEyeIcon.className = 'fa-regular fa-eye';
    }
  });
}

if (formEl) {
  formEl.addEventListener('submit', function(e) {
    if (!licenseValid && licKeyInput && licKeyInput.value.trim()) {
      e.preventDefault();
      licKeyInput.classList.add('is-invalid');
      return;
    }
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.classList.add('loading');
      submitBtn.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i><span>Connecting...</span>';
    }
  });
}

var fade = document.getElementById('page-fade');
var backLink = document.getElementById('back-link');
if (backLink) {
  backLink.addEventListener('click', function(e) {
    e.preventDefault();
    if (fade) fade.classList.add('active');
    setTimeout(function() { location.href = '/'; }, 180);
  });
}

generateQR();

window.addEventListener('pageshow', function() {
  if (fade) fade.classList.remove('active');
  if (submitBtn) {
    submitBtn.disabled = false;
    submitBtn.classList.remove('loading');
    submitBtn.innerHTML = '<span>Continue</span><i class="fa-solid fa-arrow-right"></i>';
  }
});