// Nav behaviour shared with taxid.co.ke: solid background on scroll, mobile menu.
(function () {
  'use strict';
  var nav = document.getElementById('nav');
  var burger = document.getElementById('nav-burger');
  if (!nav || !burger) return;

  function onScroll() { nav.classList.toggle('solid', window.scrollY > 16); }
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  function closeMenu() {
    nav.classList.remove('menu-open');
    burger.setAttribute('aria-expanded', 'false');
  }

  burger.addEventListener('click', function () {
    var open = nav.classList.toggle('menu-open');
    burger.setAttribute('aria-expanded', String(open));
  });

  Array.prototype.forEach.call(document.querySelectorAll('#nav-links a'), function (a) {
    a.addEventListener('click', closeMenu);
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && nav.classList.contains('menu-open')) {
      closeMenu();
      burger.focus();
    }
  });
})();
