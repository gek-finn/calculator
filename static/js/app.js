// Общая клиентская логика (вспомогательные функции).
window.LP = {
  money(value) {
    const n = Number(value);
    if (!isFinite(n)) return String(value);
    return n.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  },
  debounce(fn, ms) {
    let t;
    return (...args) => {
      clearTimeout(t);
      t = setTimeout(() => fn(...args), ms);
    };
  },
};
