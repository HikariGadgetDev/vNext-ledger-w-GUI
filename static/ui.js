// static/ui.js

// --- CSRF (safe, no-regex) ---
function getCookie(name){
  const key = name + "=";
  const cookies = (document.cookie || "").split(";").map(s => s.trim());
  for (const c of cookies) {
    if (c.startsWith(key)) return decodeURIComponent(c.slice(key.length));
  }
  return null;
}

document.body.addEventListener("htmx:configRequest", function(evt){
  const verb = (evt.detail.verb || "").toUpperCase();
  if (verb === "GET" || verb === "HEAD" || verb === "OPTIONS") return;

  const token = getCookie("csrf_token"); // app.py の CSRF_COOKIE
  if (!token) return;

  evt.detail.headers["x-csrf-token"] = token; // app.py の CSRF_HEADER
});


// 10秒自動消去（show を付けてから 10秒後に外す）
document.body.addEventListener('htmx:afterSwap', function(evt){
  if(evt.detail.target.id === 'result'){
    const resultDiv = document.getElementById('result');
    if(!resultDiv) return;
    resultDiv.classList.add('show');
    setTimeout(() => resultDiv.classList.remove('show'), 10000);
  }
});

// scan 完了後に filters を refresh（path を見て判定）
document.body.addEventListener('htmx:afterRequest', function(evt){
  const path = evt.detail?.pathInfo?.requestPath || evt.detail?.requestConfig?.path || "";
  if(path.includes('/scan')){
    const el = document.getElementById('filters');
    if(el && window.htmx){
      htmx.trigger(el, 'refresh');
    }
  }
});

// closeModal 関数（show 外す + display none + innerHTML 空）
function closeModal(){
  const m = document.getElementById('modal');
  if(!m) return;
  m.classList.remove('show');
  m.style.display = 'none';
  m.innerHTML = "";
}

// Modal helpers (keep for compatibility)
function openModal(html){
  const m = document.getElementById('modal');
  m.innerHTML = html;
  m.classList.add('show');
  m.style.display = 'block';
}

// Sort: keep state in hidden inputs, then refresh filters
function sortToggle(key){
  const s = document.getElementById('sort');
  const d = document.getElementById('dir');
  if(!s || !d) return;
  if(s.value === key){
    d.value = (d.value === 'asc') ? 'desc' : 'asc';
  } else {
    s.value = key;
    d.value = 'asc';
  }
  if(window.htmx){ htmx.trigger(document.getElementById('filters'),'refresh'); }
}

// DOMContentLoaded で初期化
document.addEventListener('DOMContentLoaded', function(){
  // Clear filters（実物の ID: q/status/priority/since/until）
  var clearBtn = document.getElementById('clearFilters');
  if(clearBtn){
    clearBtn.addEventListener('click', function(){
      document.getElementById('q').value = '';
      document.getElementById('status').value = '';
      document.getElementById('priority').value = '';
      document.getElementById('since').value = '';
      document.getElementById('until').value = '';
      // filters を refresh
      if(window.htmx){
        htmx.trigger(document.getElementById('filters'), 'refresh');
      }
    });
  }
  
  // Modal close on backdrop click
  var modal = document.getElementById('modal');
  if(modal){
    modal.addEventListener('click', function(e){
      if(e.target === modal){
        closeModal();
      }
    });
  }
});