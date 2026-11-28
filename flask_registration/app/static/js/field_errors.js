/*
  Accessibility (docs/toegankelijkheid.md): a field with an error is linked to
  its error text. Every field that turns red (.gu-input--error, from the
  server or from a check in the page) gets aria-invalid="true" and an
  aria-describedby to the visible error text (.gu-join__formError) in its
  own field block; when the error goes away, both go too. On load the focus
  moves to the first field with an error, unless something else on the
  page already has the focus. Without JavaScript the forms work as before:
  the server checks every form and shows the error texts.
*/
(function () {
  var FIELD = '.gu-input--error';
  var ERROR = '.gu-join__formError';
  var n = 0;

  function visible(el) {
    return !el.hidden && el.style.display !== 'none' && el.getClientRects().length > 0;
  }

  // The error text of a field: the nearest visible one in the same field block.
  function errorFor(field) {
    var box = field.parentElement;
    for (var depth = 0; box && depth < 3; depth++, box = box.parentElement) {
      var texts = box.querySelectorAll(ERROR);
      for (var i = 0; i < texts.length; i++) {
        if (visible(texts[i])) return texts[i];
      }
      if (box.tagName === 'FORM') break;
    }
    return null;
  }

  function ids(field) {
    return (field.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean);
  }

  function sync(field) {
    var linked = field.getAttribute('data-gu-error-id');
    var list = ids(field).filter(function (id) { return id !== linked; });
    if (field.matches(FIELD)) {
      if (!field.hasAttribute('aria-invalid')) field.setAttribute('data-gu-invalid', 'js');
      field.setAttribute('aria-invalid', 'true');
      var text = errorFor(field);
      if (text) {
        if (!text.id) text.id = 'guFieldError' + (++n);
        list.push(text.id);
        field.setAttribute('data-gu-error-id', text.id);
      }
    } else {
      if (field.getAttribute('data-gu-invalid') === 'js') field.removeAttribute('aria-invalid');
      field.removeAttribute('data-gu-invalid');
      field.removeAttribute('data-gu-error-id');
    }
    if (list.length) field.setAttribute('aria-describedby', list.join(' '));
    else field.removeAttribute('aria-describedby');
  }

  function all() {
    document.querySelectorAll(FIELD + ', [data-gu-invalid], [data-gu-error-id]').forEach(sync);
  }

  function start() {
    all();
    var first = document.querySelector(FIELD + ', [aria-invalid="true"]');
    var busy = document.activeElement && document.activeElement !== document.body;
    if (first && !busy && typeof first.focus === 'function') {
      first.focus({ preventScroll: true });
      first.scrollIntoView({ block: 'center' });
    }
    // Checks in the page add and remove the error class: follow them.
    new MutationObserver(function (changes) {
      changes.forEach(function (c) {
        var t = c.target;
        if (t.matches && (t.matches(FIELD) || t.hasAttribute('data-gu-invalid') || t.hasAttribute('data-gu-error-id'))) sync(t);
      });
    }).observe(document.body, { attributes: true, attributeFilter: ['class'], subtree: true });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
