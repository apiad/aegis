- **An open tab takes the new client by itself after an upgrade.** A tab
  that stayed open through an upgrade, or a browser that kept an old copy,
  ran the old client, and new features did nothing until a hard refresh. The
  page now knows its build and the server names its own when the tab
  reconnects. When they differ, the page takes the new build once, with or
  without a service worker.
