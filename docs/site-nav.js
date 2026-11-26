/* 全站頁面切換按鈕（右上角）
 * 每一頁在 </body> 前加一行：<script src="site-nav.js"></script>
 * 新增頁面時只要在下面 PAGES 加一行，所有頁面的選單會一起更新。
 */
(function () {
  const PAGES = [
    { href: 'watch.html',    name: '每日觀察',   sub: '燈號＋壓力階段，每天先看這頁' },
    { href: 'escape.html',   name: '逃生檢查表', sub: '台美股逃生／回升階段' },
    { href: 'index.html',    name: '美元流動性', sub: '供給側 × 需求側' },
    { href: 'cycle.html',    name: '流動性循環', sub: 'Fed 資產負債表、政策利率、股市與信用' },
    { href: 'calendar.html', name: '債務日曆',   sub: 'QRA、TGA、標售、債務上限' },
    { href: 'treasury.html', name: '美債危機',   sub: 'TLT／IEF 危機燈號' },
  ];

  let here = location.pathname.split('/').pop() || 'index.html';
  if (!here.endsWith('.html')) here = 'index.html';
  const cur = PAGES.find(p => p.href === here);

  const css = `
  #snav{position:fixed;top:calc(10px + env(safe-area-inset-top));right:calc(10px + env(safe-area-inset-right));z-index:9999;
    font-family:'Noto Sans TC','PingFang TC','Microsoft JhengHei',sans-serif}
  #snav-btn{display:flex;align-items:center;gap:7px;background:#111B2D;color:#D9E2EF;border:1px solid #2A3A56;
    border-radius:8px;padding:7px 11px;font-size:13px;cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.35);font-family:inherit}
  #snav-btn:hover,#snav.open #snav-btn{border-color:#3FB8AF}
  #snav-btn .bars{display:inline-block;width:14px;height:10px;position:relative}
  #snav-btn .bars::before,#snav-btn .bars::after,#snav-btn .bars i{content:'';position:absolute;left:0;right:0;height:2px;background:#3FB8AF;border-radius:1px}
  #snav-btn .bars::before{top:0} #snav-btn .bars i{top:4px} #snav-btn .bars::after{top:8px}
  #snav-menu{display:none;position:absolute;right:0;top:calc(100% + 6px);min-width:250px;background:#0E1626;
    border:1px solid #2A3A56;border-radius:10px;padding:6px;box-shadow:0 10px 30px rgba(0,0,0,.5)}
  #snav.open #snav-menu{display:block}
  #snav-menu a{display:block;padding:8px 10px;border-radius:7px;text-decoration:none;color:#D9E2EF;line-height:1.35}
  #snav-menu a:hover{background:#111B2D}
  #snav-menu a.cur{background:rgba(63,184,175,.14);box-shadow:inset 3px 0 0 #3FB8AF}
  #snav-menu b{display:block;font-size:14px;font-weight:500}
  #snav-menu span{display:block;font-size:11.5px;color:#7E8CA3}
  #snav-menu .hd{font-size:10.5px;letter-spacing:.14em;color:#4A5670;padding:4px 10px 6px}`;
  const st = document.createElement('style');
  st.textContent = css;
  document.head.appendChild(st);

  const box = document.createElement('div');
  box.id = 'snav';
  box.innerHTML =
    `<button id="snav-btn" type="button" aria-haspopup="true" aria-expanded="false"><span class="bars"><i></i></span>${cur ? cur.name : '切換頁面'}</button>` +
    `<div id="snav-menu" role="menu"><div class="hd">LIQUIDITY DASHBOARD</div>` +
    PAGES.map(p => `<a role="menuitem" href="${p.href}" class="${p === cur ? 'cur' : ''}"><b>${p.name}</b><span>${p.sub}</span></a>`).join('') +
    `</div>`;
  document.body.appendChild(box);

  const btn = box.querySelector('#snav-btn');
  const toggle = open => { box.classList.toggle('open', open); btn.setAttribute('aria-expanded', open); };
  btn.addEventListener('click', e => { e.stopPropagation(); toggle(!box.classList.contains('open')); });
  document.addEventListener('click', e => { if (!box.contains(e.target)) toggle(false); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') toggle(false); });

  // 按鈕蓋到頁面標題時，把整頁往下推一點
  const basePad = parseFloat(getComputedStyle(document.body).paddingTop) || 0;
  function avoidOverlap() {
    document.body.style.paddingTop = basePad + 'px';
    const b = btn.getBoundingClientRect();
    // 手機窄螢幕：一律把頁面往下推，按鈕自己一列，不會蓋到後來才載入的文字
    if (innerWidth < 760) { document.body.style.paddingTop = (basePad + b.height + 10) + 'px'; return; }
    // 用文字實際占的範圍判斷（整條 header 很寬，但右邊常是空的）
    const hit = [...document.querySelectorAll('h1, header')].some(el => {
      if (box.contains(el)) return false;
      const rg = document.createRange();
      rg.selectNodeContents(el);
      return [...rg.getClientRects()].some(r => r.width && r.right > b.left - 6 && r.left < b.right && r.top < b.bottom + 4 && r.bottom > b.top);
    });
    if (hit) document.body.style.paddingTop = (basePad + b.height + 10) + 'px';
  }
  avoidOverlap();
  addEventListener('resize', avoidOverlap);
  addEventListener('load', avoidOverlap);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(avoidOverlap);
  setTimeout(avoidOverlap, 2500);   // 有些頁面的標題列文字是抓完資料才填進去
})();
