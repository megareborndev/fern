var modal = document.getElementById('pay-modal');
var currentPlan = 'lifetime';

function showModal() {
  document.getElementById('pay-methods-box').style.display = '';
  document.getElementById('checkout-box').style.display = 'none';
  modal.classList.add('show');
}

function closeModal() { modal.classList.remove('show'); }

function backToMethods() {
  document.getElementById('pay-methods-box').style.display = '';
  document.getElementById('checkout-box').style.display = 'none';
}

var MONTHLY_FEATS = [
  '<li><i class="fa-solid fa-check"></i> All features</li>',
  '<li><i class="fa-solid fa-check"></i> End-to-end encryption</li>',
  '<li><i class="fa-solid fa-check"></i> Friends & DMs</li>',
  '<li><i class="fa-solid fa-check"></i> Cancel anytime</li>'
].join('');
var LIFETIME_FEATS = [
  '<li><i class="fa-solid fa-check"></i> Lifetime updates</li>',
  '<li><i class="fa-solid fa-check"></i> All features</li>',
  '<li><i class="fa-solid fa-check"></i> End-to-end encryption</li>',
  '<li><i class="fa-solid fa-check"></i> Friends & DMs</li>',
  '<li><i class="fa-solid fa-check"></i> No hidden fees</li>',
  '<li><i class="fa-solid fa-check"></i> Priority support</li>'
].join('');

function switchPlan(plan) {
  if (plan === 'month') return;
  currentPlan = plan;
  var isMonth = plan === 'month';
  document.getElementById('switch-month').classList.toggle('active', isMonth);
  document.getElementById('switch-lifetime').classList.toggle('active', !isMonth);
  document.getElementById('pc-sn').textContent = isMonth ? '1 month license' : 'Lifetime license';
  document.getElementById('pc-amt').textContent = isMonth ? '$3' : '$5';
  document.getElementById('pc-sub').textContent = isMonth ? 'first month, then $5/mo' : 'one-time, never expires';
  document.getElementById('pc-feats').innerHTML = isMonth ? MONTHLY_FEATS : LIFETIME_FEATS;
  document.getElementById('pc-note').innerHTML = isMonth
    ? 'Use code <b>FERN</b> at checkout to claim your first month for $3.'
    : 'Limited launch offer for the first 50 users before price increases. Upon purchase you\'ll receive a unique lifetime license key that never expires.';
  document.getElementById('pc-pay').innerHTML = isMonth
    ? '<div class="pay-method best" onclick="payPlan(\'month\')"><i class="fa-brands fa-bitcoin"></i><div class="pm-info"><b>Crypto</b><span>BTC, ETH, LTC and more</span></div><span class="pm-badge">Cheapest</span></div><div class="pay-method" onclick="payPlan(\'month\')"><i class="fa-solid fa-credit-card"></i><div class="pm-info"><b>Card</b><span>Debit or credit</span></div><span class="pm-badge">+7% fee</span></div>'
    : '<div class="pay-method best" onclick="payPlan(\'lifetime\')"><i class="fa-brands fa-bitcoin"></i><div class="pm-info"><b>Crypto</b><span>BTC, ETH, LTC and more</span></div><span class="pm-badge">Cheapest</span></div><div class="pay-method" onclick="payPlan(\'lifetime\')"><i class="fa-solid fa-credit-card"></i><div class="pm-info"><b>Card</b><span>Debit or credit</span></div><span class="pm-badge">+7% fee</span></div>';
}

function payPlan(plan) {
  currentPlan = plan;
  var title = document.getElementById('pay-plan-title');
  var sub = document.getElementById('pay-plan-sub');
  if (plan === 'month') {
    title.textContent = 'Get your 1 month license';
    sub.textContent = 'How do you want to pay?';
  } else {
    title.textContent = 'Get your lifetime license';
    sub.textContent = 'How do you want to pay?';
  }
  showModal();
}

function payCrypto() {
  closeModal();
  if (window.sellAuth) {
    var cart = currentPlan === 'month'
      ? [{ productId: 824850, variantId: 1415901, quantity: 1 }]
      : [{ productId: 824852, variantId: 1415903, quantity: 1 }];
    sellAuth.open({
      shopId: 258907,
      shopUrl: 'https://fern.mysellauth.com',
      cart: cart,
      theme: 'dark',
      returnUrl: window.location.origin + '/confirm'
    });
  } else {
    startCheckout();
  }
}

function payCard() {
  startCheckout();
}

function payTicket() {
  window.location.href = '/invite';
}

function startCheckout() {
  var isMonth = currentPlan === 'month';
  document.getElementById('pay-methods-box').style.display = 'none';
  document.getElementById('checkout-box').style.display = '';
  document.getElementById('checkout-plan-sub').textContent = isMonth ? '$3 1 month license (code FERN)' : '$5 lifetime license';
  document.getElementById('embed-month').style.display = isMonth ? '' : 'none';
  document.getElementById('embed-lifetime').style.display = isMonth ? 'none' : '';
  var embed = document.querySelector(isMonth ? '#embed-month button' : '#embed-lifetime button');
  if (embed) embed.click();
}

document.addEventListener('aquadratic:success', function(e) {
  window.location.href = '/confirm';
});

document.addEventListener('sellauth:success', function(e) {
  window.location.href = '/confirm';
});

modal.addEventListener('click', function(e) { if (e.target === modal) closeModal(); });
document.addEventListener('keydown', function(e) { if (e.key === 'Escape') closeModal(); });

var navbar = document.getElementById('navbar');
window.addEventListener('scroll', function() {
  if (window.scrollY > 100) navbar.classList.add('shrunk');
  else navbar.classList.remove('shrunk');
});

var fade = document.getElementById('page-fade');
function goTo(url) {
  if (!fade) { location.href = url; return; }
  fade.classList.add('active');
  setTimeout(function() { location.href = url; }, 320);
}
document.querySelectorAll('a.nav-icon-btn[href="/login"], a.nav-power-btn[href="/login"], a.open-btn[href="/login"], a.btn-green[href="/login"], .btn-green:not([href])').forEach(function(el) {
  el.addEventListener('click', function(e) {
    e.preventDefault();
    goTo('/login');
  });
});
window.addEventListener('pageshow', function() {
  if (fade) fade.classList.remove('active');
});

var fernImg = document.querySelector('.fern-float');
if (fernImg && window.matchMedia && window.matchMedia('(min-width: 641px) and (hover: hover) and (pointer: fine)').matches) {
  var fernHit = new Image();
  fernHit.src = fernImg.src;
  fernHit.onload = function() {
    var cv = document.createElement('canvas');
    cv.width = fernHit.naturalWidth;
    cv.height = fernHit.naturalHeight;
    var cvt = cv.getContext('2d');
    cvt.drawImage(fernHit, 0, 0);
    var px = cvt.getImageData(0, 0, cv.width, cv.height).data;
    var hovered = false;
    var lastTouch = 0;
    fernImg.style.pointerEvents = 'none';
    document.addEventListener('mousemove', function(e) {
      var r = fernImg.getBoundingClientRect();
      var ix = Math.floor((e.clientX - r.left) / r.width * cv.width);
      var iy = Math.floor((e.clientY - r.top) / r.height * cv.height);
      var inside = ix >= 0 && iy >= 0 && ix < cv.width && iy < cv.height;
      var alpha = inside ? px[(iy * cv.width + ix) * 4 + 3] : 0;
      if (alpha > 4) lastTouch = e.timeStamp;
      if (!hovered && alpha > 80) {
        hovered = true;
        fernImg.style.pointerEvents = 'auto';
      } else if (hovered && e.timeStamp - lastTouch > 120) {
        hovered = false;
        fernImg.style.pointerEvents = 'none';
      }
    });
    document.addEventListener('mouseleave', function() {
      hovered = false;
      fernImg.style.pointerEvents = 'none';
    });
  };
}

window.switchPlan = switchPlan;
window.payPlan = payPlan;
window.closeModal = closeModal;
window.payCard = payCard;
window.payCrypto = payCrypto;
window.payTicket = payTicket;
window.backToMethods = backToMethods;