/* Клиентская логика шага «Начать расчёт» и «Показать итог».
   Все денежные расчёты выполняются на сервере (POST /quote/*), здесь — только ввод
   и отображение. Это исключает расхождение между браузером и Python-логикой. */
(function () {
  'use strict';
  const PRICE = window.PRICE;
  if (!PRICE) return;

  const $ = (sel) => document.querySelector(sel);
  const state = {
    structures: [],   // добавленные конструкции
    editIndex: null,  // редактируемая строка или null
    lastQuote: null,
  };

  // ---------- шаг 2: показ рабочей области ----------
  const workspace = $('#calc-workspace');
  const startBtn = $('#btn-start-calc');
  if (startBtn) {
    startBtn.addEventListener('click', () => {
      workspace.hidden = false;
      startBtn.hidden = true;
      workspace.scrollIntoView({ behavior: 'smooth' });
    });
  }
  if (workspace) workspace.hidden = false;

  // ---------- заполнение выпадающих списков ----------
  function fillSelect(sel, items, placeholder) {
    sel.innerHTML = '';
    if (placeholder) {
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = placeholder;
      sel.appendChild(opt);
    }
    Object.values(items).forEach((item) => {
      if (item.активна === false || item.активен === false || item.активно === false) return;
      const opt = document.createElement('option');
      opt.value = item.id;
      let label = item.название;
      if (item.цена_за_погонный_метр !== undefined) {
        label += item.цена_за_погонный_метр ? ` — ${item.цена_за_погонный_метр} ₽/пог. м` : ' — цена не заполнена';
      }
      if (item.надбавка_процент !== undefined) label += ` — +${item.надбавка_процент}%`;
      opt.textContent = label;
      sel.appendChild(opt);
    });
  }

  const fConstruction = $('#f-construction');
  const fProfile = $('#f-profile');
  const fGlass = $('#f-glass');
  const fHardware = $('#f-hardware');
  if (!fConstruction) return;

  fillSelect(fConstruction, PRICE.constructions);
  fillSelect(fProfile, PRICE.profiles);
  fillSelect(fGlass, PRICE.glasses);

  // чипы фурнитуры
  fHardware.innerHTML = '';
  Object.values(PRICE.hardware).forEach((h) => {
    if (h.активна === false) return;
    const chip = document.createElement('label');
    chip.className = 'chip';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.dataset.id = h.id;
    const qty = document.createElement('input');
    qty.type = 'number';
    qty.min = '1';
    qty.value = '1';
    qty.disabled = true;
    chip.append(cb, document.createTextNode(`${h.название} (${h.цена ?? 'цена?'} ₽)`), qty);
    cb.addEventListener('change', () => { qty.disabled = !cb.checked; });
    fHardware.appendChild(chip);
  });

  // ---------- услуги ----------
  const servicesList = $('#services-list');
  servicesList.innerHTML = '';
  Object.values(PRICE.services).forEach((s) => {
    if (s.активна === false) return;
    const label = document.createElement('label');
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.dataset.id = s.id;
    // Проживание (0 ₽) по умолчанию выключено — необязательная услуга
    cb.checked = !(s.цена === '0' || s.цена === 0);
    label.append(cb, document.createTextNode(`${s.название} — ${s.цена} ₽ за ${s.единица}`));
    servicesList.appendChild(label);
  });

  // ---------- скидка ----------
  const discountType = $('#discount-type');
  const discountValue = $('#discount-value');
  const discountHint = $('#discount-hint');
  discountType.addEventListener('change', () => {
    discountValue.disabled = discountType.value === '';
    const maxPct = PRICE.rules['макс_скидка_менеджера_процент'] ?? '—';
    discountHint.textContent = discountType.value
      ? `Максимальная скидка по правилам прайса: ${maxPct}%. Превышение будет отклонено сервером.`
      : '';
  });

  // ---------- форма конструкции ----------
  const formWrap = $('#structure-form-wrap');
  const preview = $('#f-preview');
  $('#btn-add-structure').addEventListener('click', () => openForm());
  $('#btn-cancel-structure').addEventListener('click', () => { formWrap.hidden = true; });

  function openForm(index) {
    state.editIndex = (index === undefined ? null : index);
    $('#structure-form-title').textContent =
      state.editIndex === null ? 'Новая конструкция' : 'Редактирование конструкции';
    formWrap.hidden = false;
    if (state.editIndex !== null) {
      const s = state.structures[state.editIndex];
      fConstruction.value = s.construction_id;
      fProfile.value = s.profile_id || '';
      fGlass.value = s.glass_id || '';
      $('#f-width').value = s.width_mm;
      $('#f-height').value = s.height_mm;
      $('#f-quantity').value = s.quantity;
      fHardware.querySelectorAll('.chip').forEach((chip) => {
        const cb = chip.querySelector('input[type=checkbox]');
        const qty = chip.querySelector('input[type=number]');
        const item = (s.hardware || []).find((h) => h.hardware_id === cb.dataset.id);
        cb.checked = !!item;
        qty.disabled = !cb.checked;
        qty.value = item ? item.quantity : 1;
      });
      $('#btn-save-structure').textContent = 'Сохранить изменения';
    } else {
      $('#btn-save-structure').textContent = 'Добавить в расчёт';
    }
    updatePreview();
    formWrap.scrollIntoView({ behavior: 'smooth' });
  }

  function collectFormData() {
    const hardware = [];
    fHardware.querySelectorAll('.chip').forEach((chip) => {
      const cb = chip.querySelector('input[type=checkbox]');
      if (cb.checked) {
        hardware.push({
          hardware_id: cb.dataset.id,
          quantity: Number(chip.querySelector('input[type=number]').value) || 1,
        });
      }
    });
    return {
      construction_id: fConstruction.value,
      profile_id: fProfile.value,
      glass_id: fGlass.value,
      width_mm: Number($('#f-width').value),
      height_mm: Number($('#f-height').value),
      quantity: Number($('#f-quantity').value) || 1,
      hardware,
    };
  }

  // промежуточный расчёт на сервере при изменении параметров
  const updatePreview = LP.debounce(async () => {
    try {
      const res = await fetch('/quote/preview_structure', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(collectFormData()),
      });
      const data = await res.json();
      if (data.errors && data.errors.length) {
        preview.textContent = '⚠ ' + data.errors.map((e) => e.message).join('; ');
      } else {
        preview.textContent =
          `Площадь: ${Number(data.area_total).toFixed(2)} м², промежуточная стоимость: ` +
          `${LP.money(data.subtotal)} ₽`;
      }
    } catch (e) {
      preview.textContent = '';
    }
  }, 500);

  [fConstruction, fProfile, fGlass, '#f-width', '#f-height', '#f-quantity'].forEach((sel) => {
    const el = typeof sel === 'string' ? $(sel) : sel;
    el.addEventListener('input', updatePreview);
    el.addEventListener('change', updatePreview);
  });

  // ---------- таблица конструкций ----------
  function renderStructures() {
    const tbody = $('#structures-table tbody');
    tbody.innerHTML = '';
    state.structures.forEach((s, i) => {
      const area = (Number(s.width_mm) * Number(s.height_mm) / 1e6) * Number(s.quantity);
      const cname = (PRICE.constructions[s.construction_id] || {}).название || s.construction_id;
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${i + 1}</td>
        <td>${cname}</td>
        <td>${s.width_mm} × ${s.height_mm}</td>
        <td>${s.quantity}</td>
        <td>${area.toFixed(2)}</td>
        <td class="row-cost">—</td>
        <td>
          <button class="btn btn-small btn-ghost" data-action="edit">Изменить</button>
          <button class="btn btn-small btn-ghost" data-action="delete">Удалить</button>
        </td>`;
      tr.querySelector('[data-action=edit]').addEventListener('click', () => openForm(i));
      tr.querySelector('[data-action=delete]').addEventListener('click', () => {
        state.structures.splice(i, 1);
        renderStructures();
      });
      tbody.appendChild(tr);
    });
  }

  $('#btn-save-structure').addEventListener('click', () => {
    const data = collectFormData();
    if (!data.construction_id) { alert('Выберите тип конструкции'); return; }
    if (state.editIndex === null) state.structures.push(data);
    else state.structures[state.editIndex] = data;
    formWrap.hidden = true;
    state.editIndex = null;
    renderStructures();
  });

  // ---------- сборка запроса ----------
  function collectQuotePayload() {
    return {
      client: {
        name: $('#client-name').value.trim(),
        phone: $('#client-phone').value.trim(),
        email: $('#client-email').value.trim(),
        address: $('#client-address').value.trim(),
        source: $('#client-source').value.trim(),
        comment: $('#client-comment').value.trim(),
      },
      structures: state.structures,
      hardware: state.structures.flatMap((s) => s.hardware || []),
      services: Array.from(servicesList.querySelectorAll('input[type=checkbox]')).map((cb) => ({
        service_id: cb.dataset.id,
        enabled: cb.checked,
      })),
      discount_type: discountType.value,
      discount_value: discountValue.value,
    };
  }

  // ---------- шаг 3: показать итог ----------
  $('#btn-show-total').addEventListener('click', async () => {
    if (!state.structures.length) { alert('Добавьте хотя бы одну конструкцию'); return; }
    const payload = collectQuotePayload();
    if (!payload.client.name) { alert('Укажите ФИО или название компании клиента'); return; }
    const res = await fetch('/quote/calculate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const quote = await res.json();
    state.lastQuote = quote;
    const totalBlock = $('#total-block');
    const empty = $('#total-empty');
    if (!quote.ok) {
      totalBlock.hidden = true;
      empty.hidden = false;
      empty.innerHTML = '<strong>Расчёт содержит ошибки:</strong><ul>' +
        quote.errors.map((e) => `<li>${e.message}</li>`).join('') + '</ul>';
      empty.scrollIntoView({ behavior: 'smooth' });
      return;
    }
    empty.hidden = true;
    totalBlock.hidden = false;
    renderTotal(quote);
    // подсветка стоимостей в таблице конструкций
    const rows = document.querySelectorAll('#structures-table tbody tr');
    quote.structures.forEach((s, i) => {
      if (rows[i]) rows[i].querySelector('.row-cost').textContent = `${LP.money(s.subtotal)} ₽`;
    });
    $('#step3').scrollIntoView({ behavior: 'smooth' });
  });

  function renderTotal(quote) {
    const details = $('#total-details');
    let html = '<h3>Расшифровка</h3><table class="breakdown-table">';
    quote.structures.forEach((s) => {
      html += `<tr><td colspan="3"><strong>${s.construction_name}</strong> — ${s.quantity} шт, ` +
              `${Number(s.area_total).toFixed(2)} м²</td></tr>`;
      s.breakdown.forEach((line) => {
        html += `<tr><td>${line.name}</td><td><small>${line.formula}</small></td>` +
                `<td style="text-align:right">${LP.money(line.amount)} ₽</td></tr>`;
      });
      (s.notes || []).forEach((n) => {
        html += `<tr><td colspan="3"><small class="muted">📌 ${n}</small></td></tr>`;
      });
    });
    quote.hardware.lines.forEach((line) => {
      html += `<tr><td>Фурнитура: ${line.name}</td><td><small>${line.formula}</small></td>` +
              `<td style="text-align:right">${LP.money(line.amount)} ₽</td></tr>`;
    });
    quote.services.lines.forEach((line) => {
      html += `<tr><td>Услуга: ${line.name}</td><td><small>${line.formula}</small></td>` +
              `<td style="text-align:right">${LP.money(line.amount)} ₽</td></tr>`;
    });
    html += '</table>';
    details.innerHTML = html;
    $('#sum-subtotal').textContent = `${LP.money(quote.subtotal)} ₽`;
    $('#sum-discount').textContent = `−${LP.money(quote.discount)} ₽`;
    $('#sum-total').textContent = `${LP.money(quote.total)} ₽`;
  }

  $('#btn-back-edit').addEventListener('click', () => {
    $('#step2').scrollIntoView({ behavior: 'smooth' });
  });

  // ---------- сохранение ----------
  $('#btn-save-quote').addEventListener('click', async () => {
    const res = await fetch('/quote/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(collectQuotePayload()),
    });
    const data = await res.json();
    const out = $('#save-result');
    if (data.ok) {
      out.innerHTML = `<div class="flash flash-success">Расчёт <strong>${data.number}</strong> ` +
        `сохранён в истории. <a href="/calculations">Открыть список</a></div>` +
        `<div class="form-actions">` +
        `<a class="btn btn-ghost" href="/calculations/${data.calc_id}/export/pdf">Экспорт расчёта (PDF)</a>` +
        `<a class="btn btn-ghost" href="/calculations/${data.calc_id}/export/xlsx">Экспорт расчёта (Excel)</a>` +
        `</div>`;
    } else {
      out.innerHTML = '<div class="flash flash-error">Не удалось сохранить:<ul>' +
        data.errors.map((e) => `<li>${e.message}</li>`).join('') + '</ul></div>';
    }
  });

  renderStructures();
})();
