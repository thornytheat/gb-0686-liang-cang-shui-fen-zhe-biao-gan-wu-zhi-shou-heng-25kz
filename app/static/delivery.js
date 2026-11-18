const m8Feedback = document.querySelector('#feedback');
function showError(message) { if (m8Feedback) m8Feedback.textContent = message || '操作失败'; }

const registerForm = document.querySelector('#register-form');
registerForm?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = registerForm.querySelector('button');
  button.disabled = true; button.textContent = '登记中';
  try {
    const response = await fetch('/api/m8/register', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ run_id: registerForm.dataset.run }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.message || '登记失败');
    location.reload();
  } catch (error) { showError(error.message); }
  finally { button.disabled = false; button.textContent = '按已复核仓批登记提货账户'; }
});

const takeForm = document.querySelector('#take-form');
const takeResult = document.querySelector('#take-result');
takeForm?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = takeForm.querySelector('button');
  button.disabled = true; button.textContent = '提交中';
  const payload = {
    run_id: takeForm.dataset.run,
    bin: takeForm.elements.bin.value,
    taken_dry_kg: takeForm.elements.taken_dry_kg.value,
    idempotency_key: takeForm.elements.idempotency_key.value.trim(),
  };
  try {
    const response = await fetch('/api/m8/take', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.message || '提货失败');
    // 成功（含幂等重放）后刷新，余量与凭证表始终以服务端为准。
    location.reload();
  } catch (error) {
    showError(error.message);
    if (takeResult) { takeResult.hidden = false; takeResult.innerHTML = '<b>本次提交未生效</b><small>扣量与开单已回滚，可用相同幂等键重试。</small>'; }
  } finally { button.disabled = false; button.textContent = '提交提货并开凭证'; }
});
