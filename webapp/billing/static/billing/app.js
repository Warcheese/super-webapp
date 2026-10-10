(() => {
  const form = document.querySelector('#invoice-form');
  if (!form) return;
  const list = document.querySelector('#item-list');
  const management = document.querySelector('#id_items-TOTAL_FORMS');
  const templates = JSON.parse(document.querySelector('#service-data').textContent);
  const picker = document.querySelector('#service-picker');
  for (const service of templates) {
    const option = document.createElement('option');
    option.value = service.id;
    option.textContent = `${service.description} · ${Number(service.price).toLocaleString('de-DE', {minimumFractionDigits: 2})} €`;
    picker.append(option);
  }
  function addRow() {
    const index = Number(management.value);
    if (index >= 100) { alert('Maximal 100 Leistungen pro Rechnung.'); return null; }
    const template = document.querySelector('#item-template').innerHTML.replaceAll('__prefix__', index);
    list.insertAdjacentHTML('beforeend', template);
    management.value = index + 1;
    return list.lastElementChild;
  }
  function total() {
    let sum = 0;
    for (const row of list.querySelectorAll('.item-card')) {
      if (row.querySelector('[name$="-DELETE"]')?.checked) continue;
      const price = Number((row.querySelector('[name$="-price"]')?.value || '').replace(',', '.'));
      const quantity = Number((row.querySelector('[name$="-quantity"]')?.value || '').replace(',', '.'));
      if (Number.isFinite(price * quantity)) sum += Math.round((price * quantity + Number.EPSILON) * 100) / 100;
    }
    document.querySelector('#live-total').textContent = sum.toLocaleString('de-DE', {style: 'currency', currency: 'EUR'});
  }
  document.querySelector('#add-item').addEventListener('click', () => { addRow(); total(); });
  document.querySelector('#add-service').addEventListener('click', () => {
    const service = templates.find(t => String(t.id) === picker.value);
    if (!service) return;
    const blank = [...list.querySelectorAll('.item-card')].find(row => !row.querySelector('[name$="-description"]').value && !row.querySelector('[name$="-DELETE"]')?.checked);
    const row = blank || addRow();
    if (!row) return;
    for (const key of ['code', 'description', 'price']) row.querySelector(`[name$="-${key}"]`).value = service[key];
    row.querySelector('[name$="-quantity"]').value = '1';
    const now = new Date();
    row.querySelector('[name$="-date"]').value = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`;
    total();
  });
  form.addEventListener('input', total);
  form.addEventListener('change', () => {
    for (const row of list.querySelectorAll('.item-card')) {
      const deleted = row.querySelector('[name$="-DELETE"]')?.checked;
      row.style.opacity = deleted ? '.45' : '1';
      // Deleted rows must remain submitted, but browser validation should not block deletion.
      for (const input of row.querySelectorAll('input:not([type=hidden]):not([type=checkbox])')) {
        if (deleted) { if (!('wasRequired' in input.dataset)) input.dataset.wasRequired = input.required ? '1' : '0'; input.required = false; }
        else if (input.dataset.wasRequired === '1') input.required = true;
      }
    }
    total();
  });
  total();
})();


// Keep each row in place while changing its status, without reloading the list.
(() => {
  for (const form of document.querySelectorAll('.status-form')) {
    const button = form.querySelector('button');
    const state = form.querySelector('[name="active"]');
    const error = document.createElement('span');
    error.className = 'status-error';
    error.setAttribute('role', 'alert');
    form.append(error);
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (button.disabled) return;
      button.disabled = true;
      error.textContent = '';
      try {
        const response = await fetch(form.action, {
          method: 'POST', body: new FormData(form), credentials: 'same-origin',
          headers: {'Accept': 'application/json'}
        });
        if (!response.ok || !response.headers.get('Content-Type')?.includes('application/json')) {
          throw new Error('Status konnte nicht gespeichert werden. Bitte erneut versuchen oder neu anmelden.');
        }
        const data = await response.json();
        button.textContent = data.label;
        button.classList.toggle('paid', data.active);
        button.classList.toggle('draft', !data.active);
        button.setAttribute('aria-label', `${form.dataset.statusName} ${data.active ? 'archivieren' : 'aktivieren'}`);
        state.value = data.active ? '0' : '1';
      } catch (failure) {
        error.textContent = 'Status konnte nicht gespeichert werden. Bitte erneut versuchen oder neu anmelden.';
      } finally {
        button.disabled = false;
      }
    });
  }
})();
