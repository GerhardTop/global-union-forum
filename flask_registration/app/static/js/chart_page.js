/* Background figures, step 1f: the chart page without reloads.

   The controls are one GET form (issues/_chart_block.html): view, period and
   the legend buttons send ?zet=<change> with the current state in hidden
   fields. Without JavaScript the page reloads. Here the form is caught: the
   same URL is fetched with fragment=1, the block is swapped, the address is
   updated (history.replaceState), focus stays on the pressed button, and the
   live region says briefly what changed. */
(function () {
  'use strict';
  var live = document.querySelector('[data-chart-live]');

  function block() { return document.querySelector('[data-chart-block]'); }

  document.addEventListener('submit', function (event) {
    var form = event.target.closest('[data-chart-form]');
    if (!form || !window.fetch || !window.URLSearchParams) return;
    event.preventDefault();
    var pressed = event.submitter;
    var params = new URLSearchParams(new FormData(form));
    if (pressed && pressed.name) params.set(pressed.name, pressed.value);
    params.set('fragment', '1');
    var value = pressed ? pressed.value : null;
    fetch(form.action + '?' + params.toString(), {headers: {'X-Requested-With': 'fetch'}, credentials: 'same-origin'})
      .then(function (resp) {
        if (!resp.ok) throw new Error(resp.status);
        return resp.text();
      })
      .then(function (html) {
        var holder = document.createElement('div');
        holder.innerHTML = html.trim();
        var fresh = holder.querySelector('[data-chart-block]');
        var old = block();
        if (!fresh || !old) throw new Error('no block');
        old.replaceWith(fresh);
        if (fresh.dataset.url && window.history && history.replaceState) {
          history.replaceState(null, '', fresh.dataset.url);
        }
        if (value) {
          var again = fresh.querySelector('button[name="zet"][value="' + CSS.escape(value) + '"]');
          if (again) again.focus();
        }
        if (live) {
          live.textContent = '';
          window.setTimeout(function () { live.textContent = fresh.dataset.announce || ''; }, 50);
        }
      })
      .catch(function () {
        /* Fall back to a normal page load. */
        form.submit();
      });
  });
})();
