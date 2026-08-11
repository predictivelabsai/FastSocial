(() => {
  const root = document.querySelector('.planner-drop-root');
  if (!root) return;
  let postId = '';
  root.addEventListener('dragstart', (event) => {
    const post = event.target.closest('[data-post-id]');
    if (!post) return;
    postId = post.dataset.postId;
    event.dataTransfer.effectAllowed = 'move';
  });
  root.addEventListener('dragover', (event) => {
    const day = event.target.closest('[data-date]');
    if (!day || !postId) return;
    event.preventDefault();
    day.classList.add('drop-target');
  });
  root.addEventListener('dragleave', (event) => {
    event.target.closest('[data-date]')?.classList.remove('drop-target');
  });
  root.addEventListener('drop', async (event) => {
    const day = event.target.closest('[data-date]');
    if (!day || !postId) return;
    event.preventDefault();
    const body = new URLSearchParams({
      csrf: root.dataset.csrf,
      post_id: postId,
      target_date: day.dataset.date,
    });
    const response = await fetch('/api/planner/reschedule', {
      method: 'POST', body, credentials: 'same-origin',
      headers: {'HX-Request': 'true'},
    });
    if (response.ok) window.location.reload();
  });
})();
