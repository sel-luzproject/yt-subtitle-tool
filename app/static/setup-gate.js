// Redirects to the first-run setup wizard until the customer has configured their API key + channel.
(async () => {
  try {
    const s = await fetch('/api/setup').then(r => r.json());
    if (!s.setup_complete) location.href = '/setup.html';
  } catch (e) {}
})();
