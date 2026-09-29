(() => {
  const getCookie = (name) => document.cookie.split('; ').find((row) => row.startsWith(`${name}=`))?.split('=')[1];
  const send = async (url, data) => {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'X-CSRFToken': decodeURIComponent(getCookie('csrftoken') || ''), 'X-Requested-With': 'XMLHttpRequest' },
      body: new URLSearchParams(data),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Une erreur est survenue.');
    return result;
  };

  const updateCount = (count) => {
    const badge = document.getElementById('cart-count');
    if (badge) badge.textContent = count;
  };

  document.querySelectorAll('.add-cart-form').forEach((form) => {
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const button = form.querySelector('.add-cart-button');
      const feedback = form.querySelector('.form-feedback');
      button.disabled = true;
      feedback.textContent = '';
      try {
        const data = await send(form.action, Object.fromEntries(new FormData(form)));
        updateCount(data.count);
        feedback.textContent = 'La pièce a rejoint votre panier.';
        button.innerHTML = 'Ajouté au panier <span>✓</span>';
      } catch (error) {
        feedback.textContent = error.message;
      } finally {
        button.disabled = false;
      }
    });
  });

  const feedback = document.querySelector('.cart-feedback');
  const syncCart = async (data) => {
    updateCount(data.count);
    const subtotal = document.getElementById('cart-subtotal');
    if (subtotal) subtotal.textContent = `${Number(data.subtotal).toLocaleString('fr-FR')} FCFA`;
    for (const row of document.querySelectorAll('[data-cart-line]')) {
      const item = data.lines.find((line) => line.key === row.dataset.cartLine);
      if (!item) row.remove();
      else {
        const price = row.querySelector('.cart-line-price');
        if (price) price.textContent = `${Number(item.line_total).toLocaleString('fr-FR')} FCFA`;
      }
    }
    if (!data.count && document.querySelector('.cart-lines')) window.location.reload();
  };

  document.querySelectorAll('.cart-quantity').forEach((input) => {
    input.addEventListener('change', async () => {
      if (!feedback) return;
      feedback.textContent = '';
      try {
        const data = await send('/panier/modifier/', { key: input.dataset.key, quantity: input.value });
        await syncCart(data);
      } catch (error) {
        feedback.textContent = error.message;
        window.location.reload();
      }
    });
  });

  document.querySelectorAll('[data-remove]').forEach((button) => {
    button.addEventListener('click', async () => {
      if (!feedback) return;
      feedback.textContent = '';
      try {
        const data = await send('/panier/modifier/', { key: button.dataset.remove, action: 'remove' });
        await syncCart(data);
      } catch (error) {
        feedback.textContent = error.message;
      }
    });
  });
})();