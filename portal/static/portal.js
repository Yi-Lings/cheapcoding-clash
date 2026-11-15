document.addEventListener('click', async event => {
    const button = event.target.closest('[data-copy]');
    if (!button) return;
    const container = button.closest('.copy-line');
    const input = container.querySelector('input');
    const message = container.nextElementSibling;
    try {
        await navigator.clipboard.writeText(input.value);
        message.textContent = '已复制';
    } catch {
        input.focus();
        input.select();
        message.textContent = '已选中，请使用设备的复制功能。';
    }
});

(() => {
    const picker = document.querySelector('.range-picker');
    if (!picker) return;
    const start = picker.querySelector('input[name="start_date"]');
    const end = picker.querySelector('input[name="end_date"]');
    const presets = picker.querySelector('.range-presets');
    const buttons = presets.querySelectorAll('button');
    const summary = picker.querySelector('summary');
    const update = () => {
        end.setCustomValidity(end.value && start.value && end.value < start.value
            ? '结束日期不能早于开始日期。' : '');
        buttons.forEach(button => {
            button.setAttribute('aria-pressed', String(button.dataset.start === start.value &&
                button.dataset.end === end.value));
        });
    };
    presets.hidden = false;
    presets.addEventListener('click', event => {
        const button = event.target.closest('button[data-start]');
        if (!button) return;
        start.value = button.dataset.start;
        end.value = button.dataset.end;
        update();
    });
    start.addEventListener('input', update);
    end.addEventListener('input', update);
    picker.addEventListener('toggle', () => {
        if (!picker.open) {
            start.value = start.defaultValue;
            end.value = end.defaultValue;
            update();
        }
    });
    document.addEventListener('click', event => {
        if (picker.open && !picker.contains(event.target)) picker.open = false;
    });
    document.addEventListener('keydown', event => {
        if (picker.open && event.key === 'Escape') {
            picker.open = false;
            summary.focus();
        }
    });
})();
(() => {
    const search = document.getElementById('traffic-user-search');
    if (!search) return;
    const field = search.closest('.traffic-user-field');
    const toggle = field.querySelector('.traffic-user-toggle');
    const results = document.getElementById('traffic-user-results');
    function setExpanded(expanded) {
        results.hidden = !expanded;
        toggle.setAttribute('aria-expanded', String(expanded));
    }

    function close() {
        search.value = search.defaultValue;
        setExpanded(false);
    }

    function show(query) {
        const all = results.querySelector('[data-all-users]').closest('li');
        const empty = results.querySelector('.traffic-user-empty');
        const entries = Array.from(results.querySelectorAll('[data-client]'), link => ({
            link,
            row: link.closest('li'),
            name: link.querySelector('span').textContent.toLowerCase(),
        }));

        const id = query.replace(/^#/, '') || query;
        all.hidden = Boolean(query);
        for (const {link, row, name} of entries) {
            row.hidden = Boolean(query) && !name.includes(query) && !link.dataset.client.includes(id);
        }
        empty.hidden = !query || entries.some(({row}) => !row.hidden);
        setExpanded(true);
    }

    function visibleLinks() {
        return Array.from(results.querySelectorAll('li:not([hidden]) > a'));
    }

    function showAll() {
        search.value = search.defaultValue;
        show('');
    }

    toggle.hidden = false;
    toggle.addEventListener('click', () => {
        if (results.hidden) showAll();
        else close();
    });
    search.addEventListener('input', () => show(search.value.trim().toLowerCase()));
    field.addEventListener('keydown', event => {
        if (event.isComposing) return;
        if (event.key === 'Escape' && !results.hidden) {
            event.preventDefault();
            close();
        } else if (event.key === 'ArrowDown') {
            event.preventDefault();
            if (results.hidden) showAll();
            visibleLinks()[0]?.focus();
        } else if (event.key === 'Enter' && event.target === search && !results.hidden) {
            visibleLinks()[0]?.click();
        }
    });
    results.addEventListener('keydown', event => {
        if (event.key === 'Escape') {
            event.preventDefault();
            close();
            search.focus();
        } else if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
            event.preventDefault();
            const links = visibleLinks();
            const index = links.indexOf(document.activeElement);
            if (event.key === 'ArrowUp' && index <= 0) search.focus();
            else links[index + (event.key === 'ArrowDown' ? 1 : -1)]?.focus();
        }
    });
    document.addEventListener('pointerdown', event => {
        if (!results.hidden && !field.contains(event.target) && !results.contains(event.target)) close();
    });
})();
(() => {
    const chart = document.querySelector('.traffic-interactive');
    if (!chart) return;
    const scroll = chart.querySelector('.traffic-scroll');
    const tooltip = chart.querySelector('.traffic-tooltip');
    const fields = ['date', 'up', 'down', 'total', 'status'].map(key =>
        [key, tooltip.querySelector('[data-field="' + key + '"]')]);
    let current = null;
    let pinned = false;
    let pendingTouch = null;
    let timer = 0;

    function show(day, keep = false) {
        if (current !== day) {
            current?.classList.remove('is-current');
            day.classList.add('is-current');
            fields.forEach(([key, field]) => { field.textContent = day.dataset[key]; });
            current = day;
        }
        pinned = keep;
        tooltip.hidden = false;
        const bounds = chart.getBoundingClientRect();
        const column = day.getBoundingClientRect();
        const width = tooltip.offsetWidth;
        const middle = column.left - bounds.left + column.width / 2;
        const side = middle + 10 + width <= bounds.width ? middle + 10 : middle - width - 10;
        tooltip.style.left = Math.max(0, Math.min(side, bounds.width - width)) + 'px';
    }

    function hide() {
        current?.classList.remove('is-current');
        current = null;
        pinned = false;
        tooltip.hidden = true;
    }

    function stopPress() {
        window.clearTimeout(timer);
        pendingTouch = null;
    }

    function column(event) {
        return event.target.closest('.traffic-day');
    }

    scroll.addEventListener('pointerover', event => {
        const day = column(event);
        if (event.pointerType === 'mouse' && day) show(day);
    });
    scroll.addEventListener('pointerleave', () => {
        if (!pinned && !scroll.contains(document.activeElement)) hide();
    });
    scroll.addEventListener('focusin', event => {
        const day = column(event);
        if (day && !pendingTouch) show(day, current === day && pinned);
    });
    scroll.addEventListener('focusout', event => {
        if (!scroll.contains(event.relatedTarget) && !pinned) hide();
    });
    scroll.addEventListener('click', event => {
        const day = column(event);
        if (day) show(day, true);
    });
    scroll.addEventListener('pointerdown', event => {
        const day = column(event);
        if (!day || !['touch', 'pen'].includes(event.pointerType)) return;
        stopPress();
        pendingTouch = {id: event.pointerId, x: event.clientX, y: event.clientY};
        timer = window.setTimeout(() => {
            if (pendingTouch) show(day, true);
        }, 430);
    });
    scroll.addEventListener('pointermove', event => {
        if (pendingTouch?.id === event.pointerId &&
            Math.hypot(event.clientX - pendingTouch.x, event.clientY - pendingTouch.y) > 10) stopPress();
    });
    scroll.addEventListener('pointerup', stopPress);
    scroll.addEventListener('pointercancel', stopPress);
    scroll.addEventListener('scroll', () => { stopPress(); hide(); }, {passive: true});
    scroll.addEventListener('contextmenu', event => {
        if (pendingTouch || (pinned && event.pointerType !== 'mouse')) event.preventDefault();
    });
    document.addEventListener('pointerdown', event => {
        if (!chart.contains(event.target)) hide();
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && current) {
            stopPress();
            hide();
        }
    });
    window.addEventListener('resize', () => {
        if (current) show(current, pinned);
    });
})();
(() => {
    const drawer = document.getElementById('portal-drawer');
    if (typeof drawer?.showModal !== 'function') return;
    const shell = drawer.closest('.portal-shell');
    const opener = shell.querySelector('.portal-menu-open');
    const mobile = window.matchMedia('(max-width: 53.125rem)');

    shell.classList.add('drawer-ready');
    opener.addEventListener('click', () => {
        if (!mobile.matches || drawer.open) return;
        drawer.showModal();
        opener.setAttribute('aria-expanded', 'true');
        drawer.querySelector('[aria-current="page"]')?.focus();
    });
    drawer.addEventListener('close', () => {
        opener.setAttribute('aria-expanded', 'false');
    });
    if (!('closedBy' in drawer)) {
        let pressedOutside = false;
        const isOutside = event => {
            const bounds = drawer.getBoundingClientRect();
            return event.clientX < bounds.left || event.clientX >= bounds.right ||
                event.clientY < bounds.top || event.clientY >= bounds.bottom;
        };
        drawer.addEventListener('pointerdown', event => { pressedOutside = isOutside(event); });
        drawer.addEventListener('pointerup', event => {
            if (pressedOutside && isOutside(event)) drawer.close();
            pressedOutside = false;
        });
        drawer.addEventListener('pointercancel', () => { pressedOutside = false; });
    }
    mobile.addEventListener('change', () => {
        if (!mobile.matches && drawer.open) drawer.close();
    });
    window.addEventListener('pageshow', event => {
        if (event.persisted && drawer.open) drawer.close();
    });
})();
