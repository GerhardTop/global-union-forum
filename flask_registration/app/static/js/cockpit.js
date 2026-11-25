/*
  The cockpit frame shared by the issue page (issues/show.html) and the
  proposal page (issues/proposal.html): one implementation, so both behave the
  same.

  - Tabs on a narrow screen ([data-tab] buttons, [data-panel] panels). Wide,
    all columns sit side by side and the tabs do nothing. Without JavaScript
    everything stacks.
  - Enlarging a column in the page ([data-expand] / [data-collapse], Escape):
    one panel wide under the phase bar, the rest away.
  - "Show more" lists ([data-more-list] with a [data-more] arrow): a few in
    the column, about 15 in the enlarged view.
  - Long facts clamped to a few lines with a per-fact more/less.
  - Disclosures ([data-disclosure] buttons with aria-controls), such as the
    collapsed voting rules card.
  - A sort select with [data-autosubmit] submits on change (no button).
  - The "+" of the facts column ([data-fact-add]) toggles the add form
    ([data-fact-add-box]) at the top of the enlarged facts, enlarging the
    column first when it is compact; [data-fact-add-close] closes it. Without
    JavaScript the "+" and the close links are plain links.

  Usage: var cockpit = GU.cockpit(rootElement, {defaultTab: 'facts'});
  The root may carry data-expanded="<panel>" to open enlarged (for example
  after filtering or sorting the enlarged proposals). Returns {show, expand,
  collapse}.
*/
(function () {
  'use strict';
  var GU = window.GU = window.GU || {};
  var BIG = 15;

  GU.cockpit = function (root, opts) {
    opts = opts || {};
    var tablist = root.querySelector('.gu-cockpit__tabs');
    var tabs = Array.prototype.slice.call(root.querySelectorAll('.gu-cockpit__tab'));
    var defaultTab = opts.defaultTab || (tabs[0] && tabs[0].dataset.tab);

    // ── Tabs ──────────────────────────────────────────────────────────
    function show(name, focus) {
      tabs.forEach(function (tab) {
        var on = tab.dataset.tab === name;
        tab.setAttribute('aria-selected', on ? 'true' : 'false');
        tab.tabIndex = on ? 0 : -1;
        if (on && focus) tab.focus();
      });
      root.querySelectorAll('[data-panel]').forEach(function (panel) {
        panel.classList.toggle('is-shown', panel.dataset.panel === name);
      });
      clampFacts();
    }
    tabs.forEach(function (tab, i) {
      tab.addEventListener('click', function () { show(tab.dataset.tab); });
      tab.addEventListener('keydown', function (e) {
        var step = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
        if (!step) return;
        e.preventDefault();
        show(tabs[(i + step + tabs.length) % tabs.length].dataset.tab, true);
      });
    });
    if (tablist) tablist.hidden = false;
    root.classList.add('gu-cockpit--tabs');

    // ── Long facts: a few lines, with "more/less" only when there is more ─
    function clampFacts() {
      root.querySelectorAll('.gu-cockpit__facts .gu-fact').forEach(function (fact) {
        if (fact.dataset.clampReady) return;
        var titled = fact.classList.contains('gu-fact--titled');
        if (!titled && (fact.hidden || !fact.offsetParent)) return;  // measuring needs it visible
        var text = fact.querySelector('.gu-fact__text:not([hidden])');  // a translated fact also carries its original, hidden
        var btn = fact.querySelector('[data-fact-more]');
        if (!text || !btn) return;
        fact.classList.add('is-clamped');
        var source = fact.querySelector('.gu-fact__source');
        var reveal = titled || (text.scrollHeight - text.clientHeight > 2) || !!source;
        if (reveal) {
          btn.hidden = false;
          btn.addEventListener('click', function () {
            var collapsed = fact.classList.toggle('is-clamped');
            btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
            btn.querySelector('[data-more-on]').hidden = !collapsed;
            btn.querySelector('[data-more-off]').hidden = collapsed;
          });
        } else {
          fact.classList.remove('is-clamped');
        }
        fact.dataset.clampReady = '1';
      });
    }

    // ── "Show more" lists ─────────────────────────────────────────────
    var lists = Array.prototype.slice.call(root.querySelectorAll('[data-more-list]'));
    function showList(list, n) {
      var items = list.children, more = list.parentNode.querySelector('[data-more]');
      list.dataset.shown = n;
      for (var i = 0; i < items.length; i++) items[i].hidden = i >= n;
      if (more) more.hidden = n >= items.length;
    }
    function baseCount(list) {
      return root.classList.contains('gu-cockpit--expanded') && list.closest('.is-expanded')
        ? BIG : Number(list.dataset.step);
    }
    lists.forEach(function (list) {
      Array.prototype.forEach.call(list.children, function (li) { li.classList.remove('gu-cockpit__extra'); });
      showList(list, baseCount(list));
      var more = list.parentNode.querySelector('[data-more]');
      if (more) more.addEventListener('click', function () {
        var step = list.closest('.is-expanded') ? BIG : 10;
        showList(list, Number(list.dataset.shown) + step);
        clampFacts();
      });
    });
    function resetLists() { lists.forEach(function (list) { showList(list, baseCount(list)); }); clampFacts(); }

    // ── Enlarge one panel in the page; the shrink icon (or Escape) goes back ─
    function expand(name, focus) {
      root.querySelectorAll('[data-panel].is-expanded').forEach(function (p) { p.classList.remove('is-expanded'); });
      root.classList.add('gu-cockpit--expanded');
      root.querySelectorAll('[data-panel="' + name + '"]').forEach(function (p) { p.classList.add('is-expanded'); });
      show(name);
      resetLists();
      if (focus !== false) {
        var shrink = root.querySelector('[data-panel="' + name + '"] [data-collapse]');
        if (shrink) shrink.focus();
      }
    }
    function collapse() {
      var was = root.querySelector('[data-panel].is-expanded [data-expand]');
      root.classList.remove('gu-cockpit--expanded');
      root.querySelectorAll('[data-panel].is-expanded').forEach(function (p) { p.classList.remove('is-expanded'); });
      resetLists();
      if (was) was.focus();
    }
    root.querySelectorAll('[data-expand]').forEach(function (btn) {
      btn.hidden = false;
      btn.addEventListener('click', function () { expand(btn.dataset.expand); });
    });
    root.querySelectorAll('[data-collapse]').forEach(function (btn) { btn.addEventListener('click', collapse); });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && root.classList.contains('gu-cockpit--expanded')) collapse();
    });

    // ── Disclosures (the voting rules card) ───────────────────────────
    function setOpen(btn, open) {
      var body = document.getElementById(btn.getAttribute('aria-controls'));
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (body) body.hidden = !open;
    }
    root.querySelectorAll('[data-disclosure]').forEach(function (btn) {
      btn.addEventListener('click', function () { setOpen(btn, btn.getAttribute('aria-expanded') !== 'true'); });
    });

    // ── Sort on change ─────────────────────────────────────────────────
    root.querySelectorAll('select[data-autosubmit]').forEach(function (select) {
      select.addEventListener('change', function () { select.form.submit(); });
    });

    // ── Add a fact in the page ─────────────────────────────────────────
    var addBox = root.querySelector('[data-fact-add-box]');
    var addPlus = Array.prototype.slice.call(root.querySelectorAll('[data-fact-add]'));
    function setAdd(open) {
      if (!addBox) return;
      addBox.hidden = !open;
      addPlus.forEach(function (a) { a.setAttribute('aria-expanded', open ? 'true' : 'false'); });
      if (open) {
        var first = addBox.querySelector('input:not([type="hidden"]), textarea');
        if (first) first.focus();
      }
    }
    if (addBox) {
      var addPanel = addBox.closest('[data-panel]');
      addPlus.forEach(function (a) {
        a.setAttribute('aria-expanded', addBox.hidden ? 'false' : 'true');
        a.addEventListener('click', function (e) {
          e.preventDefault();
          var enlarged = root.classList.contains('gu-cockpit--expanded') && addPanel.classList.contains('is-expanded');
          if (!enlarged) { expand(addPanel.dataset.panel, false); setAdd(true); }
          else setAdd(addBox.hidden);
        });
      });
      root.querySelectorAll('[data-fact-add-close]').forEach(function (a) {
        a.addEventListener('click', function (e) {
          e.preventDefault();
          setAdd(false);
          if (addPlus[0]) addPlus[0].focus();
        });
      });
    }

    // ── Start: the tab of a linked item (or the default), maybe enlarged ─
    var target = location.hash && document.getElementById(location.hash.slice(1));
    var panel = target && target.closest('[data-panel]');
    show(panel ? panel.dataset.panel : defaultTab);
    if (target) {
      var disclosure = target.querySelector('[data-disclosure]');  // a link to #stemregels opens the rules
      if (disclosure) setOpen(disclosure, true);
    }
    if (root.dataset.expanded) expand(root.dataset.expanded, false);

    return {show: show, expand: expand, collapse: collapse};
  };
})();

/* Background figures (step 1e): the explanation of "AI selection" on a chart
   card. Hover and focus show it through CSS (also without JavaScript); a tap
   or click toggles it (aria-expanded), Escape and a tap elsewhere close it. */
(function () {
  'use strict';
  function close(wrap) {
    wrap.classList.remove('is-open');
    var btn = wrap.querySelector('button');
    if (btn) btn.setAttribute('aria-expanded', 'false');
  }
  function closeAll(except) {
    document.querySelectorAll('[data-ai-tip].is-open').forEach(function (wrap) {
      if (wrap !== except) close(wrap);
    });
  }
  document.addEventListener('click', function (event) {
    var btn = event.target.closest('[data-ai-tip] > button');
    if (!btn) { closeAll(null); return; }
    var wrap = btn.parentElement;
    var open = !wrap.classList.contains('is-open');
    closeAll(wrap);
    wrap.classList.toggle('is-open', open);
    wrap.classList.remove('is-closed');
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
  });
  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') return;
    document.querySelectorAll('[data-ai-tip]').forEach(function (wrap) {
      if (wrap.classList.contains('is-open') || wrap.contains(document.activeElement)) {
        close(wrap);
        wrap.classList.add('is-closed');   /* hide it while focus stays on the button */
      }
    });
  });
  document.addEventListener('focusout', function (event) {
    var wrap = event.target.closest && event.target.closest('[data-ai-tip]');
    if (wrap) wrap.classList.remove('is-closed');
  });
})();
