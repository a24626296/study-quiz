(function () {
  // ===== 封存 =====
  var ARCHIVE_KEY = 'archivedVideos';
  var showArchived = false;
  var allCollapsed = false;

  function loadArchivedSet() {
    try { return new Set(JSON.parse(localStorage.getItem(ARCHIVE_KEY) || '[]')); }
    catch (e) { return new Set(); }
  }
  function saveArchivedSet(setObj) {
    try { localStorage.setItem(ARCHIVE_KEY, JSON.stringify(Array.from(setObj))); } catch (e) {}
  }
  function updateArchiveButtonLabel(count) {
    var btn = document.getElementById('archive-toggle-btn');
    if (btn) btn.textContent = (showArchived ? '📂 隱藏已封存' : '📦 顯示已封存') + ' (' + count + ')';
  }
  function applyArchivedState() {
    var archived = loadArchivedSet();
    document.querySelectorAll('.item').forEach(function (el) {
      var id = el.dataset.id;
      var isArchived = archived.has(id);
      el.classList.toggle('archived', isArchived);
      var cb = el.querySelector('.archive-checkbox');
      if (cb) cb.checked = isArchived;
    });
    var list = document.getElementById('item-list');
    if (list) list.classList.toggle('show-archived', showArchived);
    updateArchiveButtonLabel(archived.size);
  }
  window.toggleArchive = function (id, checked) {
    var archived = loadArchivedSet();
    if (checked) { archived.add(id); } else { archived.delete(id); }
    saveArchivedSet(archived);
    applyArchivedState();
  };
  window.toggleShowArchived = function () {
    showArchived = !showArchived;
    applyArchivedState();
  };

  // ===== 收合 / 格狀檢視 =====
  window.toggleCollapseAll = function () {
    allCollapsed = !allCollapsed;
    document.querySelectorAll('.item').forEach(function (el) {
      el.classList.toggle('collapsed', allCollapsed);
    });
    var btn = document.getElementById('collapse-toggle-btn');
    if (btn) btn.textContent = allCollapsed ? '📂 全部展開' : '📁 全部收合';
  };

  window.toggleViewMode = function () {
    var container = document.getElementById('item-list');
    var isGrid = container.classList.toggle('view-grid');
    try { localStorage.setItem('viewMode', isGrid ? 'grid' : 'list'); } catch (e) {}
    document.getElementById('view-mode-btn').textContent = isGrid ? '📃 清單檢視' : '🔲 格狀檢視';
  };

  try {
    if (localStorage.getItem('viewMode') === 'grid') {
      var list0 = document.getElementById('item-list');
      if (list0) list0.classList.add('view-grid');
      var vbtn = document.getElementById('view-mode-btn');
      if (vbtn) vbtn.textContent = '📃 清單檢視';
    }
  } catch (e) {}

  // ===== 刪除 =====
  window.deleteVideo = function (id) {
    var pathParts = window.location.pathname.split('/').filter(Boolean);
    var repoName = pathParts[0] || '';
    var username = window.location.hostname.split('.')[0];
    var actionsUrl = 'https://github.com/' + username + '/' + repoName + '/actions/workflows/delete_video.yml';

    var doOpen = function () {
      alert('已複製影片 ID:' + id + '\n\n即將開啟「刪除線上複習頁面」的 Action 頁面,\n貼上這個 ID 到 video_id 欄位,按 Run workflow 即可刪除。');
      window.open(actionsUrl, '_blank');
    };

    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(id).then(doOpen).catch(doOpen);
    } else {
      doOpen();
    }
  };

  // ===== 資料夾(拖拉整理;純前端功能,存在瀏覽器 localStorage,
  //       每台裝置各自記自己的分類,不會透過 GitHub 同步,新影片預設在「未分類」) =====
  var FOLDER_LIST_KEY = 'videoFolderList'; // [{id, name}]
  var FOLDER_MAP_KEY = 'videoFolderMap';   // {videoId: folderId}

  function loadFolderList() {
    try { return JSON.parse(localStorage.getItem(FOLDER_LIST_KEY) || '[]'); } catch (e) { return []; }
  }
  function saveFolderList(list) {
    try { localStorage.setItem(FOLDER_LIST_KEY, JSON.stringify(list)); } catch (e) {}
  }
  function loadFolderMap() {
    try { return JSON.parse(localStorage.getItem(FOLDER_MAP_KEY) || '{}'); } catch (e) { return {}; }
  }
  function saveFolderMap(map) {
    try { localStorage.setItem(FOLDER_MAP_KEY, JSON.stringify(map)); } catch (e) {}
  }
  function escapeHtml(s) {
    var div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
  }

  window.addFolder = function () {
    var name = prompt('新資料夾名稱:', '');
    if (!name || !name.trim()) return;
    var list = loadFolderList();
    var id = 'f_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    list.push({ id: id, name: name.trim() });
    saveFolderList(list);
    renderFolders();
  };

  window.renameFolder = function (id) {
    var list = loadFolderList();
    var folder = list.filter(function (f) { return f.id === id; })[0];
    if (!folder) return;
    var name = prompt('重新命名資料夾:', folder.name);
    if (!name || !name.trim()) return;
    folder.name = name.trim();
    saveFolderList(list);
    renderFolders();
  };

  window.deleteFolder = function (id) {
    if (!confirm('確定要刪除這個資料夾嗎?裡面的影片不會被刪除,會移回「未分類」。')) return;
    var list = loadFolderList().filter(function (f) { return f.id !== id; });
    saveFolderList(list);
    var map = loadFolderMap();
    Object.keys(map).forEach(function (vid) { if (map[vid] === id) delete map[vid]; });
    saveFolderMap(map);
    renderFolders();
  };

  window.moveFolder = function (id, dir) {
    var list = loadFolderList();
    var idx = -1;
    for (var i = 0; i < list.length; i++) { if (list[i].id === id) { idx = i; break; } }
    if (idx === -1) return;
    var newIdx = idx + dir;
    if (newIdx < 0 || newIdx >= list.length) return;
    var tmp = list[idx]; list[idx] = list[newIdx]; list[newIdx] = tmp;
    saveFolderList(list);
    renderFolders();
  };

  function assignVideoToFolder(videoId, folderId) {
    var map = loadFolderMap();
    if (folderId) { map[videoId] = folderId; } else { delete map[videoId]; }
    saveFolderMap(map);
  }

  function makeDropZone(body, onDropVideo) {
    body.addEventListener('dragover', function (e) {
      e.preventDefault();
      body.classList.add('drag-over');
    });
    body.addEventListener('dragleave', function () {
      body.classList.remove('drag-over');
    });
    body.addEventListener('drop', function (e) {
      e.preventDefault();
      body.classList.remove('drag-over');
      var vid = e.dataTransfer.getData('text/plain');
      if (vid) onDropVideo(vid);
    });
  }

  function renderFolders() {
    var master = document.getElementById('item-list');
    if (!master) return;

    // 先把目前所有 .item 節點蒐集起來(不管現在巢狀在哪裡),之後直接「搬移」這些既有節點,
    // 不重新產生 HTML,這樣才不會弄丟原本的封存勾選狀態、收合狀態、拖曳監聽器。
    var allItems = {};
    master.querySelectorAll('.item').forEach(function (el) {
      allItems[el.dataset.id] = el;
    });

    var folders = loadFolderList();
    var map = loadFolderMap();

    master.innerHTML = '';
    var bodies = {};

    folders.forEach(function (folder) {
      var section = document.createElement('div');
      section.className = 'folder-section';
      section.dataset.folderId = folder.id;

      var header = document.createElement('div');
      header.className = 'folder-header';
      header.innerHTML =
        '<span class="folder-toggle">▾</span>' +
        '<span class="folder-name">📁 ' + escapeHtml(folder.name) + '</span>' +
        '<span class="folder-count"></span>' +
        '<span class="folder-actions">' +
        '<button data-act="up" title="上移">↑</button>' +
        '<button data-act="down" title="下移">↓</button>' +
        '<button data-act="rename" title="重新命名">✏️</button>' +
        '<button data-act="delete" title="刪除資料夾">🗑</button>' +
        '</span>';
      header.addEventListener('click', function (e) {
        var actBtn = e.target.closest('button');
        if (actBtn) {
          e.stopPropagation();
          var act = actBtn.dataset.act;
          if (act === 'up') window.moveFolder(folder.id, -1);
          else if (act === 'down') window.moveFolder(folder.id, 1);
          else if (act === 'rename') window.renameFolder(folder.id);
          else if (act === 'delete') window.deleteFolder(folder.id);
          return;
        }
        section.classList.toggle('folder-collapsed');
      });

      var body = document.createElement('div');
      body.className = 'folder-body';
      makeDropZone(body, function (vid) { assignVideoToFolder(vid, folder.id); renderFolders(); });

      section.appendChild(header);
      section.appendChild(body);
      master.appendChild(section);
      bodies[folder.id] = body;
    });

    // 未分類(永遠存在,不能刪除)
    var unsortedSection = document.createElement('div');
    unsortedSection.className = 'folder-section folder-unsorted';
    var unsortedHeader = document.createElement('div');
    unsortedHeader.className = 'folder-header';
    unsortedHeader.innerHTML =
      '<span class="folder-toggle">▾</span><span class="folder-name">📂 未分類</span><span class="folder-count"></span>';
    unsortedHeader.addEventListener('click', function () {
      unsortedSection.classList.toggle('folder-collapsed');
    });
    var unsortedBody = document.createElement('div');
    unsortedBody.className = 'folder-body';
    makeDropZone(unsortedBody, function (vid) { assignVideoToFolder(vid, null); renderFolders(); });
    unsortedSection.appendChild(unsortedHeader);
    unsortedSection.appendChild(unsortedBody);
    master.appendChild(unsortedSection);
    bodies.__unsorted__ = unsortedBody;

    Object.keys(allItems).forEach(function (vid) {
      var el = allItems[vid];
      var folderId = map[vid];
      var targetBody = (folderId && bodies[folderId]) ? bodies[folderId] : bodies.__unsorted__;
      targetBody.appendChild(el);
    });

    Object.keys(bodies).forEach(function (key) {
      var body = bodies[key];
      var section = body.parentElement;
      var countEl = section.querySelector('.folder-count');
      if (countEl) countEl.textContent = '(' + body.children.length + ')';
    });

    applyArchivedState();
  }

  function initDraggableItems() {
    document.querySelectorAll('.item').forEach(function (el) {
      el.setAttribute('draggable', 'true');
      el.addEventListener('dragstart', function (e) {
        e.dataTransfer.setData('text/plain', el.dataset.id);
        e.dataTransfer.effectAllowed = 'move';
        el.classList.add('dragging');
      });
      el.addEventListener('dragend', function () {
        el.classList.remove('dragging');
      });
    });
  }

  // ===== 初始化 =====
  initDraggableItems();
  renderFolders();
})();
