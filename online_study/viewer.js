(function () {
  var params = new URLSearchParams(window.location.search);
  var videoId = params.get('id');
  var startParam = params.get('t');
  var startSeconds = startParam !== null ? parseInt(startParam, 10) : null;
  if (startSeconds !== null && isNaN(startSeconds)) startSeconds = null;
  var player;
  var embedBlocked = false;
  var primaryLang = 'zh';
  var chatHistory = [];
  var systemContext = '';
  var CHAT_MODEL = 'gemini-3-flash-preview';

  // 答對題目是否收合的總開關(每次按「作答完成」都會重設為 true,
  // 也就是預設先把這次答對的題目收起來)
  var allCorrectCollapsed = true;

  // 頂部選項列摺疊狀態:記住使用者上次的選擇(這是一般靜態網站,不是
  // Claude 的 artifact 沙盒,可以正常使用 localStorage)
  window.toggleTopBar = function () {
    var content = document.getElementById('top-bar-content');
    var isOpen = content.classList.toggle('open');
    try { localStorage.setItem('topBarOpen', isOpen ? '1' : '0'); } catch (e) {}
  };
  try {
    if (localStorage.getItem('topBarOpen') === '1') {
      document.getElementById('top-bar-content').classList.add('open');
    }
  } catch (e) {}

  // 拖曳影片框調整大小時,同步更新左欄(.player-col)本身的寬度,
  // 這樣 flexbox 才會正確讓右邊題目欄跟著收縮讓出空間,而不是被蓋住。
  (function initPlayerResize() {
    var resizableEl = document.getElementById('player-resizable');
    var playerColEl = document.querySelector('.player-col');
    if (!resizableEl || !playerColEl || typeof ResizeObserver === 'undefined') return;
    var ro = new ResizeObserver(function (entries) {
      for (var i = 0; i < entries.length; i++) {
        var newWidth = Math.round(entries[i].contentRect.width);
        playerColEl.style.flex = '0 0 ' + newWidth + 'px';
        playerColEl.style.width = newWidth + 'px';
      }
    });
    ro.observe(resizableEl);
  })();

  // 自訂拖曳手把:取代瀏覽器原生的 CSS resize(手把太小、觸控裝置完全不支援)。
  // 用 Pointer Events 統一處理滑鼠跟觸控(iPad 也能正常拖曳)。
  (function initCustomResizeHandle() {
    var container = document.getElementById('player-resizable');
    var handle = document.getElementById('resize-handle');
    if (!container || !handle) return;

    var dragging = false;
    var startX = 0, startY = 0, startW = 0, startH = 0;
    var MIN_W = 260, MIN_H = 150, MAX_W = 900;

    handle.addEventListener('pointerdown', function (e) {
      dragging = true;
      startX = e.clientX;
      startY = e.clientY;
      startW = container.offsetWidth;
      startH = container.offsetHeight;
      try { handle.setPointerCapture(e.pointerId); } catch (err) {}
      e.preventDefault();
    });

    handle.addEventListener('pointermove', function (e) {
      if (!dragging) return;
      var newW = Math.min(MAX_W, Math.max(MIN_W, startW + (e.clientX - startX)));
      var newH = Math.max(MIN_H, startH + (e.clientY - startY));
      container.style.width = newW + 'px';
      container.style.height = newH + 'px';
      e.preventDefault();
    });

    function endDrag(e) {
      if (!dragging) return;
      dragging = false;
      try { handle.releasePointerCapture(e.pointerId); } catch (err) {}
    }
    handle.addEventListener('pointerup', endDrag);
    handle.addEventListener('pointercancel', endDrag);
  })();

  if (!videoId) {
    document.getElementById('app').innerHTML = '<p class="empty">網址缺少 ?id= 參數,請從複習清單頁點進來。</p>';
    return;
  }

  fetch('./data/' + encodeURIComponent(videoId) + '.json')
    .then(function (res) {
      if (!res.ok) throw new Error('找不到這支影片的資料(HTTP ' + res.status + ')');
      return res.json();
    })
    .then(render)
    .catch(function (err) {
      document.getElementById('app').innerHTML = '<p class="empty">載入失敗:' + err.message + '</p>';
    });

  function esc(text) {
    var d = document.createElement('div');
    d.textContent = text || '';
    return d.innerHTML;
  }

  function timestampToSeconds(ts) {
    if (!ts) return null;
    var m = /^\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\s*$/.exec(String(ts));
    if (!m) return null;
    var hh = m[1] ? parseInt(m[1], 10) : 0;
    var mm = parseInt(m[2], 10);
    var ss = parseInt(m[3], 10);
    return hh * 3600 + mm * 60 + ss;
  }

  // 把秒數轉回 mm:ss(超過一小時自動變成 hh:mm:ss),用來顯示在跳轉按鈕上
  function formatSeconds(totalSeconds) {
    totalSeconds = Math.max(0, Math.floor(totalSeconds));
    var hh = Math.floor(totalSeconds / 3600);
    var mm = Math.floor((totalSeconds % 3600) / 60);
    var ss = totalSeconds % 60;
    function pad(n) { return (n < 10 ? '0' : '') + n; }
    return hh ? (pad(hh) + ':' + pad(mm) + ':' + pad(ss)) : (pad(mm) + ':' + pad(ss));
  }

  function render(data) {
    document.title = (data.subject || '線上複習') + ' - 線上複習';
    document.getElementById('subject').textContent = data.subject || '';
    initPdfLink();
    initPdfViewerToolbar();

    if (data.has_transcript) {
      var link = document.createElement('a');
      link.href = './data/' + encodeURIComponent(videoId) + '.srt';
      link.download = videoId + '.srt';
      link.className = 'transcript-link';
      link.textContent = '📝 下載修正逐字稿(.srt)';
      var topBarContent = document.getElementById('top-bar-content');
      if (topBarContent) topBarContent.appendChild(link);
    }

    window.onYouTubeIframeAPIReady = function () {
      player = new YT.Player('player', {
        height: '270',
        width: '480',
        videoId: videoId,
        playerVars: {
          playsinline: 1,
          origin: window.location.origin,
          cc_load_policy: 1,   // 預設開啟字幕
          cc_lang_pref: 'zh-Hant',  // 如果影片本身有繁中字幕軌,優先用這個
        },
        events: {
          onError: onPlayerError,
          onReady: function () {
            if (startSeconds !== null) {
              player.seekTo(startSeconds, true);
              player.playVideo();
            }
          }
        }
      });
    };
    var ytScript = document.createElement('script');
    ytScript.src = 'https://www.youtube.com/iframe_api';
    document.body.appendChild(ytScript);

    renderMC(data.mc_questions || []);
    renderCloze(data.cloze_items || []);
    applyLang();

    systemContext = buildSystemContext(data);
    initChat();
  }

  function onPlayerError(event) {
    if ([101, 150, 100, 5].indexOf(event.data) !== -1) embedBlocked = true;
  }

  window.seekTo = function (seconds) {
    if (embedBlocked || !player || !player.seekTo) {
      window.open('https://www.youtube.com/watch?v=' + videoId + '&t=' + seconds + 's', '_blank');
      return;
    }
    player.seekTo(seconds, true);
    player.playVideo();
  };

  function hoverableEl(zhText, enText, cssClass) {
    var div = document.createElement('div');
    div.className = 'hoverable ' + (cssClass || '');
    div.dataset.zh = zhText || '';
    div.dataset.en = enText || '';
    var main = document.createElement('span');
    main.className = 'main-text';
    var tip = document.createElement('span');
    tip.className = 'tooltip';
    div.appendChild(main);
    div.appendChild(tip);
    return div;
  }

  // 建立「跳轉」按鈕(文字顯示實際時間點,例如 🎬 00:50)+「展開/收合」按鈕
  // 的共用區塊,選擇題跟克漏字都會用到。
  function buildHeadActions(seconds, card) {
    var headActions = document.createElement('div');
    headActions.className = 'qcard-head-actions';

    if (seconds !== null) {
      var btn = document.createElement('button');
      btn.className = 'ts-btn';
      btn.type = 'button';
      btn.textContent = '🎬 ' + formatSeconds(seconds);
      btn.onclick = function () { seekTo(seconds); };
      headActions.appendChild(btn);
    }

    var collapseToggle = document.createElement('button');
    collapseToggle.className = 'ts-btn qcard-collapse-toggle';
    collapseToggle.type = 'button';
    collapseToggle.style.display = 'none'; // 答對之後才會顯示出來
    collapseToggle.textContent = '展開';
    collapseToggle.onclick = function () {
      var collapsed = card.classList.toggle('collapsed-correct');
      collapseToggle.textContent = collapsed ? '展開' : '收合';
    };
    headActions.appendChild(collapseToggle);

    return headActions;
  }

  function renderMC(questions) {
    var container = document.getElementById('mc-list');
    if (!questions.length) {
      container.innerHTML = '<p class="empty">(無)</p>';
      return;
    }
    questions.forEach(function (q, i) {
      var seconds = timestampToSeconds(q.timestamp);
      var card = document.createElement('div');
      card.className = 'qcard';

      var head = document.createElement('div');
      head.className = 'qcard-head';
      head.innerHTML = '<span class="qnum-wrap"><span class="qnum">選擇題 ' + (i + 1) +
        '</span><span class="qcard-badge">✅ 已學會</span></span>';
      head.appendChild(buildHeadActions(seconds, card));
      card.appendChild(head);

      var body = document.createElement('div');
      body.className = 'qcard-body';
      body.appendChild(hoverableEl(q.zh_question, q.en_question, 'q-text'));

      var optsWrap = document.createElement('div');
      optsWrap.className = 'q-options';
      var correctLetter = (q.answer || '').trim().toUpperCase().slice(0, 1);
      optsWrap.dataset.correct = correctLetter;

      var zhOpts = q.zh_options || [];
      var enOpts = q.en_options || [];
      var n = Math.max(zhOpts.length, enOpts.length);
      for (var oi = 0; oi < n; oi++) {
        var letter = String.fromCharCode(65 + oi);
        var label = document.createElement('label');
        label.className = 'opt-row';
        var radio = document.createElement('input');
        radio.type = 'radio';
        radio.name = 'mc_' + i;
        radio.value = letter;
        radio.className = 'opt-radio';
        var letterSpan = document.createElement('span');
        letterSpan.className = 'opt-letter';
        letterSpan.textContent = letter;
        label.appendChild(radio);
        label.appendChild(letterSpan);
        label.appendChild(hoverableEl(zhOpts[oi] || '', enOpts[oi] || '', 'q-option'));
        optsWrap.appendChild(label);
      }
      body.appendChild(optsWrap);

      var details = document.createElement('details');
      details.innerHTML = '<summary>看答案與解析</summary>' +
        '<div class="answer">正解:(' + esc(q.answer || '') + ')</div>' +
        '<div class="explain">' + esc(q.explanation || '') + '</div>';
      body.appendChild(details);

      card.appendChild(body);
      container.appendChild(card);
    });
  }

  function renderCloze(items) {
    var container = document.getElementById('cz-list');
    if (!items.length) {
      container.innerHTML = '<p class="empty">(無)</p>';
      return;
    }
    items.forEach(function (c, i) {
      var seconds = timestampToSeconds(c.timestamp);
      var card = document.createElement('div');
      card.className = 'qcard cloze-card';

      var head = document.createElement('div');
      head.className = 'qcard-head';
      head.innerHTML = '<span class="qnum-wrap"><span class="qnum">背誦重點 ' + (i + 1) +
        '</span><span class="qcard-badge">✅ 已學會</span></span>';
      head.appendChild(buildHeadActions(seconds, card));
      card.appendChild(head);

      var body = document.createElement('div');
      body.className = 'qcard-body';

      var clozeText = c.cloze_text || '';
      var match = /\{\{c1::(.*?)\}\}/.exec(clozeText);
      var correctAnswer = match ? match[1].trim() : '';
      var maskedHtml = esc(clozeText).replace(
        /\{\{c1::(.*?)\}\}/,
        '<span class="blank"><span class="blank-inner">$1</span></span>'
      );

      var qText = document.createElement('div');
      qText.className = 'q-text';
      qText.innerHTML = maskedHtml;
      var blankEl = qText.querySelector('.blank');
      if (blankEl) {
        blankEl.onclick = function () { blankEl.classList.add('revealed'); };
      }
      body.appendChild(qText);

      var answerRow = document.createElement('div');
      answerRow.className = 'answer-input-row';
      var input = document.createElement('input');
      input.type = 'text';
      input.className = 'cloze-answer-input';
      input.placeholder = '輸入你的答案...';
      input.dataset.correct = correctAnswer;
      var feedback = document.createElement('span');
      feedback.className = 'cloze-feedback';
      answerRow.appendChild(input);
      answerRow.appendChild(feedback);
      body.appendChild(answerRow);

      var details = document.createElement('details');
      details.innerHTML = '<summary>看中文對照與解析</summary>' +
        '<div class="q-zh">' + esc(c.zh_translation || '') + '</div>' +
        '<div class="explain">' + esc(c.explanation || '') + '</div>';
      body.appendChild(details);

      var tag = document.createElement('div');
      tag.className = 'tag';
      tag.textContent = c.tags || '';
      body.appendChild(tag);

      card.appendChild(body);
      container.appendChild(card);
    });
  }

  window.setLang = function (lang) {
    primaryLang = lang;
    document.getElementById('btn-zh').classList.toggle('active', lang === 'zh');
    document.getElementById('btn-en').classList.toggle('active', lang === 'en');
    applyLang();
  };

  function applyLang() {
    var otherLang = primaryLang === 'zh' ? 'en' : 'zh';
    document.querySelectorAll('.hoverable').forEach(function (el) {
      el.querySelector('.main-text').textContent = el.dataset[primaryLang] || '';
      el.querySelector('.tooltip').textContent = el.dataset[otherLang] || '';
    });
  }

  // 根據這一題對錯,更新卡片的樣式 class 跟「展開/收合」按鈕的顯示狀態。
  // 答對:套用 qcard-correct,並依照目前的總開關狀態決定要不要收合。
  // 答錯:清掉收合狀態,展開/收合按鈕隱藏(答錯的題目不提供收合,要留著複習)。
  function markCardResult(card, isCorrect) {
    if (!card) return;
    card.classList.remove('qcard-correct', 'qcard-wrong');
    card.classList.add(isCorrect ? 'qcard-correct' : 'qcard-wrong');
    var toggle = card.querySelector('.qcard-collapse-toggle');
    if (isCorrect) {
      card.classList.toggle('collapsed-correct', allCorrectCollapsed);
      if (toggle) {
        toggle.style.display = 'inline-block';
        toggle.textContent = allCorrectCollapsed ? '展開' : '收合';
      }
    } else {
      card.classList.remove('collapsed-correct');
      if (toggle) toggle.style.display = 'none';
    }
  }

  window.finishQuiz = function () {
    var mcContainers = document.querySelectorAll('.q-options[data-correct]');
    var mcTotal = mcContainers.length;
    var mcCorrect = 0;
    mcContainers.forEach(function (el) {
      var selected = el.querySelector('input[type=radio]:checked');
      var correct = el.dataset.correct;
      el.querySelectorAll('.opt-row').forEach(function (row) {
        row.classList.remove('opt-correct', 'opt-wrong');
        var val = row.querySelector('input').value;
        if (val === correct) row.classList.add('opt-correct');
        else if (selected && val === selected.value) row.classList.add('opt-wrong');
      });
      var isCorrect = !!selected && selected.value === correct;
      if (isCorrect) mcCorrect++;
      markCardResult(el.closest('.qcard'), isCorrect);
    });

    var clozeInputs = document.querySelectorAll('.cloze-answer-input');
    var clozeTotal = clozeInputs.length;
    var clozeCorrect = 0;
    clozeInputs.forEach(function (input) {
      var userVal = (input.value || '').trim().toLowerCase();
      var correctVal = (input.dataset.correct || '').trim().toLowerCase();
      var feedback = input.parentElement.querySelector('.cloze-feedback');
      var isCorrect = !!userVal && userVal === correctVal;
      if (isCorrect) {
        clozeCorrect++;
        input.classList.add('input-correct');
        input.classList.remove('input-wrong');
        if (feedback) feedback.textContent = '✅ 正確';
      } else {
        input.classList.add('input-wrong');
        input.classList.remove('input-correct');
        if (feedback) feedback.textContent = '❌ 正確答案:' + input.dataset.correct;
      }
      markCardResult(input.closest('.qcard'), isCorrect);
    });

    var totalQ = mcTotal + clozeTotal;
    var totalCorrect = mcCorrect + clozeCorrect;
    var pct = totalQ > 0 ? Math.round((totalCorrect / totalQ) * 100) : 0;
    document.getElementById('score-summary').innerHTML =
      '選擇題 ' + mcCorrect + '/' + mcTotal + '　背誦重點 ' + clozeCorrect + '/' + clozeTotal +
      '　總分 <b>' + totalCorrect + '/' + totalQ + '</b>(' + pct + '%)';

    var collapseBtn = document.getElementById('collapse-correct-btn');
    if (collapseBtn) {
      if (totalCorrect > 0) {
        allCorrectCollapsed = true; // 每次「作答完成」都先預設收合這次答對的題目
        collapseBtn.style.display = 'inline-block';
        collapseBtn.textContent = '📂 展開已學會的題目';
      } else {
        collapseBtn.style.display = 'none';
      }
    }
  };

  // 「展開已學會的題目 / 收合已學會的題目」總開關,一次切換全部答對的卡片
  window.toggleCollapseCorrect = function () {
    allCorrectCollapsed = !allCorrectCollapsed;
    document.querySelectorAll('.qcard-correct').forEach(function (card) {
      card.classList.toggle('collapsed-correct', allCorrectCollapsed);
    });
    document.querySelectorAll('.qcard-correct .qcard-collapse-toggle').forEach(function (btn) {
      btn.textContent = allCorrectCollapsed ? '展開' : '收合';
    });
    var collapseBtn = document.getElementById('collapse-correct-btn');
    if (collapseBtn) {
      collapseBtn.textContent = allCorrectCollapsed ? '📂 展開已學會的題目' : '📁 收合已學會的題目';
    }
  };

  // ===== AI 對話框 =====
  function buildSystemContext(data) {
    var lines = [];
    lines.push('你是一位航空考試輔導助教。以下是使用者正在複習的教材內容,請根據這些內容回答問題、' +
      '解釋觀念、或做額外的延伸討論,不需要每次都重複整份教材:');
    lines.push('科目:' + (data.subject || ''));
    (data.mc_questions || []).forEach(function (q, i) {
      lines.push('選擇題' + (i + 1) + ':' + (q.zh_question || '') +
        ' 正解(' + (q.answer || '') + ') 解析:' + (q.explanation || ''));
    });
    (data.cloze_items || []).forEach(function (c, i) {
      var plain = (c.cloze_text || '').replace(/\{\{c1::(.*?)\}\}/, '$1');
      lines.push('背誦重點' + (i + 1) + ':' + plain + ' 中文:' + (c.zh_translation || ''));
    });
    return lines.join('\n');
  }

  // ===== PDF 手冊連結(存在 localStorage,每支影片各自記一個網址) =====
  function pdfLinkKey() {
    return 'pdfLink_' + videoId;
  }

  function initPdfLink() {
    var input = document.getElementById('pdf-link-input');
    var bar = document.getElementById('pdf-open-bar');
    var saved = '';
    try { saved = localStorage.getItem(pdfLinkKey()) || ''; } catch (e) {}
    if (input) input.value = saved;
    renderPdfOpenBar(saved, bar);
  }

  function renderPdfOpenBar(url, bar) {
    bar = bar || document.getElementById('pdf-open-bar');
    if (!bar) return;
    if (!url) {
      bar.innerHTML = '';
      closePdfViewer();
      return;
    }

    if (isPrivateDocLink(url)) {
      // 私人文件:不能給「新分頁開啟」的連結(那個網址沒有帶驗證,打開會失敗),
      // 只能透過內嵌檢視器用 GitHub API + 權杖去讀取。
      bar.innerHTML =
        '<span class="pdf-open-link pdf-private-badge">🔒 私人文件</span>' +
        '<button class="pdf-open-link pdf-inline-btn" id="pdf-inline-open-btn">📑 內嵌檢視(可畫重點)</button>';
    } else {
      bar.innerHTML =
        '<a class="pdf-open-link" href="' + url.replace(/"/g, '&quot;') +
        '" target="_blank" rel="noopener">📖 開啟手冊 PDF(新分頁)</a>' +
        '<button class="pdf-open-link pdf-inline-btn" id="pdf-inline-open-btn">📑 內嵌檢視(可畫重點)</button>';
    }
    var inlineBtn = document.getElementById('pdf-inline-open-btn');
    if (inlineBtn) inlineBtn.onclick = function () { openPdfViewer(url); };
  }

  window.savePdfLink = function () {
    var input = document.getElementById('pdf-link-input');
    var url = (input.value || '').trim();
    try {
      if (url) {
        localStorage.setItem(pdfLinkKey(), url);
      } else {
        localStorage.removeItem(pdfLinkKey());
      }
    } catch (e) {}
    renderPdfOpenBar(url);
  };

  // ===== 私人文件(放在 private repo,透過 GitHub API + 個人權杖讀取) =====
  // 連結格式:private:owner/repo/path/to/file.pdf#page=12
  var GH_TOKEN_STORAGE_KEY = 'ghPrivateDocsToken';

  function isPrivateDocLink(url) {
    return /^private:/.test((url || '').trim());
  }

  function parsePrivateLink(url) {
    var withoutPrefix = url.replace(/^private:/, '');
    var page = getPageFromUrl(withoutPrefix);
    var pathPart = withoutPrefix.split('#')[0];
    var segments = pathPart.split('/').filter(Boolean);
    var owner = segments.shift() || '';
    var repo = segments.shift() || '';
    var path = segments.join('/');
    return { owner: owner, repo: repo, path: path, page: page };
  }

  function getGithubToken() {
    try { return localStorage.getItem(GH_TOKEN_STORAGE_KEY) || ''; } catch (e) { return ''; }
  }
  function setGithubToken(t) {
    try { localStorage.setItem(GH_TOKEN_STORAGE_KEY, t); } catch (e) {}
  }
  function clearGithubToken() {
    try { localStorage.removeItem(GH_TOKEN_STORAGE_KEY); } catch (e) {}
  }

  function promptForGithubToken() {
    var t = prompt(
      '這是私人文件,需要 GitHub 權杖才能讀取。\n' +
      '(只需要對該私人 repo 的 Contents: Read-only 權限;貼上後會存在這台裝置的瀏覽器裡,之後不用再輸入)',
      ''
    );
    if (t && t.trim()) { setGithubToken(t.trim()); return t.trim(); }
    return '';
  }

  // 用「使用者名稱/repo」+ 檔案路徑 這種比較不容易打錯的方式,組出 private: 連結
  window.setupPrivatePdfLink = function () {
    var ownerRepo = prompt('請輸入「GitHub帳號/repo名稱」,例如 a24626296/study-private-docs:', '');
    if (!ownerRepo || ownerRepo.indexOf('/') === -1) return;
    var path = prompt('請輸入檔案在 repo 裡的路徑,例如 PrinciplesofFlightATPL-CAE.pdf:', '');
    if (!path) return;
    var page = prompt('要預設跳到第幾頁?(不填就是第 1 頁):', '');
    var link = 'private:' + ownerRepo.replace(/^\/+|\/+$/g, '') + '/' + path.replace(/^\/+/, '');
    if (page && /^\d+$/.test(page.trim())) link += '#page=' + page.trim();
    var input = document.getElementById('pdf-link-input');
    if (input) input.value = link;
    window.savePdfLink();
  };

  window.resetPrivateToken = function () {
    if (!confirm('確定要清除這台裝置已儲存的私人文件權杖嗎?下次開啟私人文件時會重新詢問。')) return;
    clearGithubToken();
    alert('已清除。');
  };

  function fetchPrivatePdfBytes(info, token) {
    var apiUrl = 'https://api.github.com/repos/' + encodeURIComponent(info.owner) + '/' +
      encodeURIComponent(info.repo) + '/contents/' +
      info.path.split('/').map(encodeURIComponent).join('/');
    return fetch(apiUrl, {
      headers: {
        'Authorization': 'Bearer ' + token,
        'Accept': 'application/vnd.github.raw+json'
      }
    }).then(function (res) {
      if (res.status === 401 || res.status === 403 || res.status === 404) {
        clearGithubToken();
        var err = new Error('AUTH_FAILED');
        err.status = res.status;
        throw err;
      }
      if (!res.ok) throw new Error('HTTP_' + res.status);
      return res.arrayBuffer();
    });
  }

  // ===== 內嵌 PDF 檢視器 + 黃色重點畫線 + 文字框 =====
  // 重點座標存成 0~1 的相對比例,換頁/縮放/重新整理都不會跑掉。
  // 改為用「PDF 網址(去掉 #page 錨點)」當 key,所以同一份文件被不同
  // 複習頁面引用時,重點是共用的(而不是各支影片各自獨立)。
  var pdfDoc = null;
  var pdfCurrentPage = 1;
  var pdfScale = 1.2;
  var pdfMode = 'highlight'; // 'highlight' | 'erase' | 'text'
  var pdfStatusTimer = null;
  var pdfHighlights = {};
  var pdfCurrentDocKey = '';

  function pdfHighlightKey(url) {
    var base = (url || '').split('#')[0].trim();
    return 'pdfHighlights::' + base;
  }

  function loadPdfHighlights(key) {
    try {
      var raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : {};
    } catch (e) {
      return {};
    }
  }

  function savePdfHighlights() {
    try { localStorage.setItem(pdfCurrentDocKey, JSON.stringify(pdfHighlights)); } catch (e) {}
  }

  function flashPdfStatus(msg) {
    var el = document.getElementById('pdfStatus');
    if (!el) return;
    el.textContent = msg;
    clearTimeout(pdfStatusTimer);
    pdfStatusTimer = setTimeout(function () { el.textContent = ''; }, 1500);
  }

  function getPageFromUrl(url) {
    var m = /#page=(\d+)/.exec(url || '');
    return m ? parseInt(m[1], 10) : 1;
  }

  // 讀取目前影片播放到第幾秒(讀不到就傳回 null,標註仍會建立,只是沒有時間戳)
  function getCurrentVideoTime() {
    try {
      if (player && typeof player.getCurrentTime === 'function') {
        return Math.round(player.getCurrentTime());
      }
    } catch (e) {}
    return null;
  }

  // 點擊「已存有影片時間戳」的標註時觸發:同一支影片就直接跳轉播放,
  // 不同影片(同一份 PDF 被別支影片引用時畫的)就開新分頁帶 ?t= 秒數跳過去。
  function jumpToAnnotation(item) {
    if (item.videoTime == null) return;
    if (!item.videoId || item.videoId === videoId) {
      seekTo(item.videoTime || 0);
      flashPdfStatus('已跳轉到影片 ' + formatSeconds(item.videoTime));
      var col = document.querySelector('.player-col');
      if (col) col.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } else {
      window.open('./viewer.html?id=' + encodeURIComponent(item.videoId) + '&t=' + item.videoTime, '_blank');
    }
  }

  function openPdfViewer(url) {
    var panel = document.getElementById('pdf-viewer-panel');
    if (!panel) return;
    if (typeof pdfjsLib === 'undefined') {
      alert('PDF 檢視元件載入失敗,請確認網路連線後重新整理頁面。');
      return;
    }
    if (pdfjsLib.GlobalWorkerOptions.workerSrc === '' || !pdfjsLib.GlobalWorkerOptions.workerSrc) {
      pdfjsLib.GlobalWorkerOptions.workerSrc =
        'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
    }

    panel.style.display = 'block';
    pdfMode = 'highlight';
    setPdfToolbarMode();
    panel.scrollIntoView({ behavior: 'smooth', block: 'start' });

    var wrap = document.getElementById('pdf-viewer-wrap');
    wrap.innerHTML = '<div class="pdf-loading">PDF 載入中…</div>';

    pdfCurrentDocKey = pdfHighlightKey(url);
    pdfHighlights = loadPdfHighlights(pdfCurrentDocKey);

    if (isPrivateDocLink(url)) {
      openPrivatePdf(url, wrap, false);
    } else {
      var cleanUrl = url.split('#')[0];
      var initialPage = getPageFromUrl(url);
      pdfjsLib.getDocument(cleanUrl).promise.then(function (doc) {
        onPdfDocLoaded(doc, initialPage);
      }).catch(function (err) {
        console.error('PDF 載入失敗:', err);
        wrap.innerHTML = '<div class="pdf-loading">PDF 載入失敗,請確認網址是否正確,或該檔案是否允許跨網域讀取。</div>';
      });
    }
  }

  function onPdfDocLoaded(doc, initialPage) {
    pdfDoc = doc;
    pdfCurrentPage = Math.min(Math.max(initialPage, 1), doc.numPages);
    document.getElementById('pdfPageNum').value = pdfCurrentPage;
    document.getElementById('pdfPageNum').max = doc.numPages;
    document.getElementById('pdfPageInfo').textContent = '/ ' + doc.numPages;
    renderPdfPage(pdfCurrentPage);
  }

  function openPrivatePdf(url, wrap, isRetry) {
    var info = parsePrivateLink(url);
    if (!info.owner || !info.repo || !info.path) {
      wrap.innerHTML = '<div class="pdf-loading">私人文件網址格式不正確,應該是「private:帳號/repo/檔案路徑」。建議用「🔒 設定私人文件」按鈕產生,比較不會打錯。</div>';
      return;
    }
    var token = getGithubToken();
    if (!token) {
      token = promptForGithubToken();
      if (!token) {
        wrap.innerHTML = '<div class="pdf-loading">沒有輸入權杖,無法讀取私人文件。</div>';
        return;
      }
    }
    fetchPrivatePdfBytes(info, token).then(function (buf) {
      return pdfjsLib.getDocument({ data: buf }).promise;
    }).then(function (doc) {
      onPdfDocLoaded(doc, info.page);
    }).catch(function (err) {
      console.error('私人 PDF 載入失敗:', err);
      if (err && err.message === 'AUTH_FAILED' && !isRetry) {
        wrap.innerHTML = '<div class="pdf-loading">權杖無效或已過期,請重新輸入…</div>';
        var newToken = promptForGithubToken();
        if (newToken) { openPrivatePdf(url, wrap, true); return; }
      }
      wrap.innerHTML = '<div class="pdf-loading">私人文件載入失敗,請確認 repo 名稱、檔案路徑、以及權杖權限是否正確。</div>';
    });
  }

  function closePdfViewer() {
    var panel = document.getElementById('pdf-viewer-panel');
    if (panel) panel.style.display = 'none';
    pdfDoc = null;
  }

  function setPdfToolbarMode() {
    var hBtn = document.getElementById('pdfHighlightModeBtn');
    var eBtn = document.getElementById('pdfEraseModeBtn');
    var tBtn = document.getElementById('pdfTextModeBtn');
    if (!hBtn || !eBtn) return;
    hBtn.classList.toggle('active', pdfMode === 'highlight');
    eBtn.classList.toggle('active', pdfMode === 'erase');
    if (tBtn) tBtn.classList.toggle('active', pdfMode === 'text');
    var canvas = document.getElementById('pdfHighlightCanvas');
    if (canvas) {
      canvas.classList.toggle('pan-mode', pdfMode === 'erase');
      canvas.style.cursor = pdfMode === 'highlight' ? 'crosshair' : (pdfMode === 'erase' ? 'cell' : 'text');
    }
  }

  function renderPdfPage(num) {
    pdfDoc.getPage(num).then(function (page) {
      var viewport = page.getViewport({ scale: pdfScale });
      var wrap = document.getElementById('pdf-viewer-wrap');
      wrap.innerHTML = '';

      var stage = document.createElement('div');
      stage.className = 'pdf-stage';
      stage.style.width = viewport.width + 'px';
      stage.style.height = viewport.height + 'px';

      var pdfCanvas = document.createElement('canvas');
      pdfCanvas.width = viewport.width;
      pdfCanvas.height = viewport.height;

      var hlCanvas = document.createElement('canvas');
      hlCanvas.id = 'pdfHighlightCanvas';
      hlCanvas.className = 'pdf-highlight-canvas';
      hlCanvas.width = viewport.width;
      hlCanvas.height = viewport.height;

      stage.appendChild(pdfCanvas);
      stage.appendChild(hlCanvas);
      wrap.appendChild(stage);

      var ctx = pdfCanvas.getContext('2d');
      page.render({ canvasContext: ctx, viewport: viewport }).promise.then(function () {
        setupPdfHighlightLayer(hlCanvas, num);
        drawPdfHighlights(hlCanvas, num);
        setPdfToolbarMode();
        document.getElementById('pdfZoomLabel').textContent = Math.round(pdfScale / 1.2 * 100) + '%';
      });
    });
  }

  function drawPdfHighlights(canvas, pageNum) {
    var ctx = canvas.getContext('2d');
    var list = pdfHighlights[pageNum] || [];
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    list.forEach(function (h) { drawPdfAnnotation(ctx, h, canvas.width, canvas.height); });
  }

  function drawPdfAnnotation(ctx, h, canvasW, canvasH) {
    if (h.type === 'text') {
      var box = computeTextBox(ctx, h.text, canvasW, canvasH);
      var x = h.x * canvasW, y = h.y * canvasH;
      ctx.fillStyle = 'rgba(255, 249, 219, 0.96)';
      ctx.strokeStyle = '#caa93a';
      ctx.lineWidth = 1.5;
      roundRect(ctx, x, y, box.w, box.h, 6);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = '#3a2f0b';
      ctx.font = box.fontSize + 'px sans-serif';
      ctx.textBaseline = 'alphabetic';
      box.lines.forEach(function (line, i) {
        ctx.fillText(line, x + box.pad, y + box.pad + box.fontSize * 0.9 + i * box.lineHeight);
      });
      drawTimeBadge(ctx, h, x, y);
    } else {
      var rx = h.x * canvasW, ry = h.y * canvasH;
      ctx.fillStyle = 'rgba(242, 201, 76, 0.45)';
      ctx.fillRect(rx, ry, h.w * canvasW, h.h * canvasH);
      drawTimeBadge(ctx, h, rx, ry);
    }
  }

  // 標註右上角的小標籤,顯示這是從影片的哪一秒畫的(▶ mm:ss),可以點擊跳轉
  function drawTimeBadge(ctx, h, boxX, boxY) {
    if (h.videoTime == null) return;
    var label = '▶ ' + formatSeconds(h.videoTime);
    ctx.font = '10px sans-serif';
    var tw = ctx.measureText(label).width;
    var ty = Math.max(2, boxY - 14);
    ctx.fillStyle = 'rgba(30,30,30,0.78)';
    ctx.fillRect(boxX, ty, tw + 10, 13);
    ctx.fillStyle = '#fff';
    ctx.textBaseline = 'alphabetic';
    ctx.fillText(label, boxX + 5, ty + 10);
  }

  // 文字框排版:依目前縮放比例決定字級跟最大寬度,換行後量出所需的框大小
  function computeTextBox(ctx, text, canvasW, canvasH) {
    var fontSize = Math.max(11, Math.round(14 * (pdfScale / 1.2)));
    ctx.font = fontSize + 'px sans-serif';
    var maxWidth = Math.min(canvasW * 0.34, 260 * (pdfScale / 1.2));
    var rawLines = String(text).split('\n');
    var lines = [];
    rawLines.forEach(function (rl) {
      lines = lines.concat(wrapCanvasText(ctx, rl, maxWidth));
    });
    var lineHeight = fontSize * 1.35;
    var widest = 0;
    lines.forEach(function (l) { widest = Math.max(widest, ctx.measureText(l).width); });
    var pad = 8;
    return {
      fontSize: fontSize,
      lines: lines,
      lineHeight: lineHeight,
      w: Math.min(maxWidth, widest) + pad * 2,
      h: lines.length * lineHeight + pad * 2,
      pad: pad
    };
  }

  // 逐字換行(中英文混排時,用逐字比對寬度比用空白斷字更準)
  function wrapCanvasText(ctx, text, maxWidth) {
    var lines = [];
    var current = '';
    for (var i = 0; i < text.length; i++) {
      var test = current + text[i];
      if (ctx.measureText(test).width > maxWidth && current) {
        lines.push(current);
        current = text[i];
      } else {
        current = test;
      }
    }
    lines.push(current);
    return lines;
  }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  // 找出點擊位置命中的標註(重點框或文字框都適用),擦除/編輯共用
  function findAnnotationAt(canvas, pageNum, pos) {
    var list = pdfHighlights[pageNum] || [];
    var ctx = canvas.getContext('2d');
    for (var i = list.length - 1; i >= 0; i--) {
      var h = list[i];
      if (h.type === 'text') {
        var box = computeTextBox(ctx, h.text, canvas.width, canvas.height);
        var x = h.x * canvas.width, y = h.y * canvas.height;
        if (pos.x >= x && pos.x <= x + box.w && pos.y >= y && pos.y <= y + box.h) {
          return { item: h, index: i };
        }
      } else {
        var px = pos.x / canvas.width, py = pos.y / canvas.height;
        if (px >= h.x && px <= h.x + h.w && py >= h.y && py <= h.y + h.h) {
          return { item: h, index: i };
        }
      }
    }
    return null;
  }

  function setupPdfHighlightLayer(canvas, pageNum) {
    var drawing = false, startX = 0, startY = 0;

    function getPos(evt) {
      var rect = canvas.getBoundingClientRect();
      var cx = evt.touches ? evt.touches[0].clientX : evt.clientX;
      var cy = evt.touches ? evt.touches[0].clientY : evt.clientY;
      return {
        x: (cx - rect.left) * (canvas.width / rect.width),
        y: (cy - rect.top) * (canvas.height / rect.height)
      };
    }

    function handleTextTap(pos) {
      var hit = findAnnotationAt(canvas, pageNum, pos);
      if (hit && hit.item.type === 'text') {
        var edited = prompt('編輯文字框內容(清空並確定 = 刪除):', hit.item.text);
        if (edited === null) return; // 取消,不動作
        if (edited.trim() === '') {
          pdfHighlights[pageNum].splice(hit.index, 1);
          flashPdfStatus('已刪除文字框');
        } else {
          hit.item.text = edited;
          flashPdfStatus('已更新文字框');
        }
        savePdfHighlights();
        drawPdfHighlights(canvas, pageNum);
        return;
      }
      var text = prompt('輸入文字框內容:', '');
      if (!text || !text.trim()) return;
      var rec = {
        type: 'text',
        x: pos.x / canvas.width,
        y: pos.y / canvas.height,
        text: text,
        videoId: videoId,
        videoTime: getCurrentVideoTime()
      };
      if (!pdfHighlights[pageNum]) pdfHighlights[pageNum] = [];
      pdfHighlights[pageNum].push(rec);
      savePdfHighlights();
      flashPdfStatus('已新增文字框');
      drawPdfHighlights(canvas, pageNum);
    }

    function down(evt) {
      evt.preventDefault();
      var pos = getPos(evt);
      startX = pos.x;
      startY = pos.y;
      if (pdfMode === 'highlight') drawing = true;
      // 擦除、文字框都在放開時(up)依「有沒有移動」判斷,這裡不用做事
    }

    function move(evt) {
      if (pdfMode === 'highlight' && !drawing) {
        // 只是滑鼠移過去(還沒按下),用來判斷要不要換成「可點擊」的手指游標
        if (!evt.touches) {
          var hoverPos = getPos(evt);
          var hoverHit = findAnnotationAt(canvas, pageNum, hoverPos);
          canvas.style.cursor = (hoverHit && hoverHit.item.videoTime != null) ? 'pointer' : 'crosshair';
        }
        return;
      }
      if (pdfMode !== 'highlight' || !drawing) return;
      evt.preventDefault();
      var pos = getPos(evt);
      var ctx = canvas.getContext('2d');
      drawPdfHighlights(canvas, pageNum);
      ctx.fillStyle = 'rgba(242, 201, 76, 0.45)';
      var rx = Math.min(startX, pos.x), ry = Math.min(startY, pos.y);
      ctx.fillRect(rx, ry, Math.abs(pos.x - startX), Math.abs(pos.y - startY));
    }

    function up(evt) {
      var pos = getPos(evt.changedTouches ? { touches: evt.changedTouches } : evt);

      if (pdfMode === 'highlight') {
        if (!drawing) return;
        drawing = false;
        var rx = Math.min(startX, pos.x), ry = Math.min(startY, pos.y);
        var rw = Math.abs(pos.x - startX), rh = Math.abs(pos.y - startY);
        var moveDist = Math.max(rw, rh);

        if (moveDist < 4) {
          // 沒有明顯拖曳,視為「點擊」:如果點到既有標註且有時間戳,就跳轉回影片
          var hit = findAnnotationAt(canvas, pageNum, pos);
          if (hit && hit.item.videoTime != null) jumpToAnnotation(hit.item);
          drawPdfHighlights(canvas, pageNum);
          return;
        }

        var rec = {
          type: 'rect',
          x: rx / canvas.width,
          y: ry / canvas.height,
          w: rw / canvas.width,
          h: rh / canvas.height,
          videoId: videoId,
          videoTime: getCurrentVideoTime()
        };
        if (!pdfHighlights[pageNum]) pdfHighlights[pageNum] = [];
        pdfHighlights[pageNum].push(rec);
        savePdfHighlights();
        flashPdfStatus('已儲存重點');
        drawPdfHighlights(canvas, pageNum);
        return;
      }

      if (pdfMode === 'erase') {
        var hitToErase = findAnnotationAt(canvas, pageNum, pos);
        if (hitToErase) {
          pdfHighlights[pageNum].splice(hitToErase.index, 1);
          savePdfHighlights();
          flashPdfStatus('已刪除');
          drawPdfHighlights(canvas, pageNum);
        }
        return;
      }

      if (pdfMode === 'text') {
        var tapDist = Math.hypot(pos.x - startX, pos.y - startY);
        if (tapDist > 8) return; // 拖曳誤觸不算,文字框只用點的
        handleTextTap(pos);
        return;
      }
    }

    canvas.addEventListener('mousedown', down);
    canvas.addEventListener('mousemove', move);
    canvas.addEventListener('mouseup', up);
    canvas.addEventListener('mouseleave', function () {
      drawing = false;
      if (pdfMode === 'highlight') canvas.style.cursor = 'crosshair';
    });
    canvas.addEventListener('touchstart', down, { passive: false });
    canvas.addEventListener('touchmove', move, { passive: false });
    canvas.addEventListener('touchend', up, { passive: false });
  }

  function goToPdfPage(num) {
    if (!pdfDoc) return;
    num = Math.min(Math.max(num, 1), pdfDoc.numPages);
    pdfCurrentPage = num;
    document.getElementById('pdfPageNum').value = num;
    renderPdfPage(num);
  }

  function initPdfViewerToolbar() {
    var hBtn = document.getElementById('pdfHighlightModeBtn');
    var eBtn = document.getElementById('pdfEraseModeBtn');
    var tBtn = document.getElementById('pdfTextModeBtn');
    var clearBtn = document.getElementById('pdfClearPageBtn');
    var prevBtn = document.getElementById('pdfPrevBtn');
    var nextBtn = document.getElementById('pdfNextBtn');
    var pageInput = document.getElementById('pdfPageNum');
    var zoomInBtn = document.getElementById('pdfZoomInBtn');
    var zoomOutBtn = document.getElementById('pdfZoomOutBtn');
    var closeBtn = document.getElementById('pdfCloseBtn');
    if (!hBtn) return;

    hBtn.onclick = function () { pdfMode = 'highlight'; setPdfToolbarMode(); };
    eBtn.onclick = function () { pdfMode = 'erase'; setPdfToolbarMode(); };
    if (tBtn) tBtn.onclick = function () { pdfMode = 'text'; setPdfToolbarMode(); };
    clearBtn.onclick = function () {
      if (!pdfDoc) return;
      var msg = '這一頁的重點/文字框是「同一份 PDF 文件」共用的,\n' +
        '清空後,所有引用同一份文件、同一頁的複習頁面都會一起被清空。\n\n確定要清空這一頁嗎?';
      if (!confirm(msg)) return;
      pdfHighlights[pdfCurrentPage] = [];
      savePdfHighlights();
      var canvas = document.getElementById('pdfHighlightCanvas');
      if (canvas) drawPdfHighlights(canvas, pdfCurrentPage);
    };
    prevBtn.onclick = function () { goToPdfPage(pdfCurrentPage - 1); };
    nextBtn.onclick = function () { goToPdfPage(pdfCurrentPage + 1); };
    pageInput.onchange = function () { goToPdfPage(parseInt(pageInput.value, 10)); };
    zoomInBtn.onclick = function () { pdfScale = Math.min(pdfScale + 0.2, 3.0); renderPdfPage(pdfCurrentPage); };
    zoomOutBtn.onclick = function () { pdfScale = Math.max(pdfScale - 0.2, 0.6); renderPdfPage(pdfCurrentPage); };
    closeBtn.onclick = closePdfViewer;
  }

  function initChat() {
    var notice = document.getElementById('chat-notice');
    var input = document.getElementById('chat-input');
    var sendBtn = document.getElementById('chat-send-btn');
    var hasKey = typeof window.PUBLIC_GEMINI_API_KEY === 'string' && window.PUBLIC_GEMINI_API_KEY.trim() !== '';

    if (!hasKey) {
      notice.textContent = '尚未設定 AI 對話金鑰,請參考 chat-config.js 裡的說明填入。';
      input.disabled = true;
      sendBtn.disabled = true;
      return;
    }

    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') sendChatMessage();
    });
  }

  function appendChatMessage(role, text) {
    var list = document.getElementById('chat-messages');
    var div = document.createElement('div');
    div.className = 'chat-msg chat-' + role;
    div.textContent = text;
    list.appendChild(div);
    list.scrollTop = list.scrollHeight;
    return div;
  }

  window.sendChatMessage = function () {
    var input = document.getElementById('chat-input');
    var text = input.value.trim();
    if (!text) return;
    input.value = '';
    appendChatMessage('user', text);
    chatHistory.push({ role: 'user', parts: [{ text: text }] });

    var sendBtn = document.getElementById('chat-send-btn');
    sendBtn.disabled = true;
    var thinkingEl = appendChatMessage('assistant', '思考中...');

    fetch('https://generativelanguage.googleapis.com/v1beta/models/' + CHAT_MODEL +
      ':generateContent?key=' + window.PUBLIC_GEMINI_API_KEY, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        systemInstruction: { parts: [{ text: systemContext }] },
        contents: chatHistory
      })
    })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        var reply;
        try {
          reply = data.candidates[0].content.parts[0].text;
        } catch (e) {
          reply = '(沒有收到有效回應,可能是額度用完或金鑰設定有誤:' + JSON.stringify(data).slice(0, 200) + ')';
        }
        thinkingEl.textContent = reply;
        chatHistory.push({ role: 'model', parts: [{ text: reply }] });
      })
      .catch(function (err) {
        thinkingEl.textContent = '發生錯誤:' + err.message;
      })
      .finally(function () {
        sendBtn.disabled = false;
      });
  };
})();
