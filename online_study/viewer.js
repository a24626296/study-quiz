(function () {
  var params = new URLSearchParams(window.location.search);
  var videoId = params.get('id');
  var player;
  var embedBlocked = false;
  var primaryLang = 'zh';
  var chatHistory = [];
  var systemContext = '';
  var CHAT_MODEL = 'gemini-3-flash-preview';

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

  function render(data) {
    document.title = (data.subject || '線上複習') + ' - 線上複習';
    document.getElementById('subject').textContent = data.subject || '';
    initPdfLink();

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
        playerVars: { playsinline: 1, origin: window.location.origin },
        events: { onError: onPlayerError }
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
      head.innerHTML = '<span class="qnum">選擇題 ' + (i + 1) + '</span>';
      if (seconds !== null) {
        var btn = document.createElement('button');
        btn.className = 'ts-btn';
        btn.textContent = '🎬 跳轉';
        btn.onclick = (function (s) { return function () { seekTo(s); }; })(seconds);
        head.appendChild(btn);
      }
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
      head.innerHTML = '<span class="qnum">背誦重點 ' + (i + 1) + '</span>';
      if (seconds !== null) {
        var btn = document.createElement('button');
        btn.className = 'ts-btn';
        btn.textContent = '🎬 跳轉';
        btn.onclick = (function (s) { return function () { seekTo(s); }; })(seconds);
        head.appendChild(btn);
      }
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
      if (selected && selected.value === correct) mcCorrect++;
    });

    var clozeInputs = document.querySelectorAll('.cloze-answer-input');
    var clozeTotal = clozeInputs.length;
    var clozeCorrect = 0;
    clozeInputs.forEach(function (input) {
      var userVal = (input.value || '').trim().toLowerCase();
      var correctVal = (input.dataset.correct || '').trim().toLowerCase();
      var feedback = input.parentElement.querySelector('.cloze-feedback');
      if (userVal && userVal === correctVal) {
        clozeCorrect++;
        input.classList.add('input-correct');
        input.classList.remove('input-wrong');
        if (feedback) feedback.textContent = '✅ 正確';
      } else {
        input.classList.add('input-wrong');
        input.classList.remove('input-correct');
        if (feedback) feedback.textContent = '❌ 正確答案:' + input.dataset.correct;
      }
    });

    var totalQ = mcTotal + clozeTotal;
    var totalCorrect = mcCorrect + clozeCorrect;
    var pct = totalQ > 0 ? Math.round((totalCorrect / totalQ) * 100) : 0;
    document.getElementById('score-summary').innerHTML =
      '選擇題 ' + mcCorrect + '/' + mcTotal + '　背誦重點 ' + clozeCorrect + '/' + clozeTotal +
      '　總分 <b>' + totalCorrect + '/' + totalQ + '</b>(' + pct + '%)';
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
    if (url) {
      bar.innerHTML = '<a class="pdf-open-link" href="' + url.replace(/"/g, '&quot;') +
        '" target="_blank" rel="noopener">📖 開啟手冊 PDF</a>';
    } else {
      bar.innerHTML = '';
    }
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
