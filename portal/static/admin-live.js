(() => {
    const list = document.getElementById('live-subscriptions');
    if (!list) return;
    const search = document.getElementById('traffic-user-search');
    const results = document.getElementById('traffic-user-results');
    const filters = new URLSearchParams(window.location.search);
    const url = new URL('/admin/operations/accounts.json', window.location.origin);
    if (filters.has('filter_client')) url.searchParams.set('filter_client', filters.get('filter_client'));
    let lastOptions = null;
    let updating = false;
    let lastCards = list.innerHTML;
    let lastNames = JSON.stringify(Array.from(document.querySelectorAll('select[name="name"]')[0]?.options ?? [], option => option.value));
    async function refresh() {
        if (document.hidden || updating) return;
        updating = true;
        try {
            const response = await fetch(url, {cache: 'no-store'});
            if (!response.ok) return;
            const {cards, names, options, selected_name: selectedName} = await response.json();
            if (results?.hidden && document.activeElement !== search) {
                if (typeof options === 'string' && options !== lastOptions) {
                    results.innerHTML = options;
                    lastOptions = options;
                }
                if (typeof selectedName === 'string' && search.defaultValue !== selectedName) {
                    search.defaultValue = selectedName;
                    search.value = selectedName;
                }
            }
            if (cards !== lastCards && !list.contains(document.activeElement?.closest("details[open]"))) {
                const expanded = new Set(Array.from(list.querySelectorAll('details[open]'), detail => detail.closest('[data-account-id]').dataset.accountId));
                list.innerHTML = cards;
                list.querySelectorAll('[data-account-id]').forEach(card => {
                    const detail = card.querySelector('details');
                    if (detail) detail.open = expanded.has(card.dataset.accountId);
                });
                lastCards = cards;
            }
            const currentNames = JSON.stringify(names);
            if (currentNames !== lastNames) {
                document.querySelectorAll('select[name="name"]').forEach(select => {
                    const selected = select.value;
                    const options = names.map(name => new Option(name, name));
                    select.replaceChildren(...options);
                    if (names.includes(selected)) select.value = selected;
                    else if (selected || filters.has('filter_client')) select.selectedIndex = -1;
                });
                lastNames = currentNames;
            }
        } catch (error) {
            if (!(error instanceof TypeError || error instanceof SyntaxError)) throw error;
        } finally {
            updating = false;
        }
    }
    window.setInterval(refresh, 3000);
    document.addEventListener('visibilitychange', refresh);
})();
