// Upload & Scan Documents: files (or phone-camera scans) -> text -> approved by the admin (Axom AI admin panel > Document Verify) -> Axom AI chat.
// This panel only uploads and shows where each document is; approving is done in the admin panel.
(function () {
    const MAX_BYTES = 20 * 1024 * 1024;
    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    let scans = [];          // pictures taken with the camera, not sent yet: [{blob, url}]
    let timer = null;

    const STATUS = {
        processing: ['Reading the text…', 'processing'],
        review: ['Waiting for admin approval', 'review'],
        approved: ['Approved', 'approved'],
        rejected: ['Rejected by admin', 'rejected'],
        failed: ['Could not be read', 'failed'],
    };

    function say(msg, bad) {
        const el = $('docMessage');
        if (!el) return;
        el.textContent = msg || '';
        el.classList.toggle('bad', !!bad);
    }

    async function api(url, opts) {
        const res = await fetch(url, opts);
        let body = null;
        try { body = await res.json(); } catch (e) { /* not JSON */ }
        if (!res.ok) throw new Error((body && (body.detail || body.error)) || ('Error ' + res.status));
        return body;
    }

    // ---------------------------------------------------------------- sending
    async function send(files) {
        if (!files.length) return;
        for (const f of files) {
            if (f.size > MAX_BYTES) { say(f.name + ' is bigger than 20 MB.', true); return; }
        }
        if (files.reduce((n, f) => n + f.size, 0) > 30 * 1024 * 1024) { say('Together these files are too big (30 MB at a time). Send them in smaller groups.', true); return; }
        const fd = new FormData();
        files.forEach((f) => fd.append('files', f, f.name));
        say('Uploading…');
        try {
            await api('/api/upload', { method: 'POST', body: fd });
            say('Uploaded. The text is being read; an admin will check it before Axom AI learns from it.');
            await refresh();
        } catch (e) {
            say(e.message, true);
        }
    }

    // A phone picture is straightened a little before it is sent: smaller, grey, contrast stretched (text reads better).
    function enhance(file) {
        return new Promise((resolve) => {
            const img = new Image();
            const url = URL.createObjectURL(file);
            img.onload = () => {
                const scale = Math.min(1, 2200 / Math.max(img.width, img.height));
                const c = document.createElement('canvas');
                c.width = Math.round(img.width * scale);
                c.height = Math.round(img.height * scale);
                const ctx = c.getContext('2d');
                ctx.drawImage(img, 0, 0, c.width, c.height);
                try {
                    const d = ctx.getImageData(0, 0, c.width, c.height);
                    const px = d.data, hist = new Array(256).fill(0);
                    for (let i = 0; i < px.length; i += 4) {
                        const g = (px[i] * 0.299 + px[i + 1] * 0.587 + px[i + 2] * 0.114) | 0;
                        px[i] = px[i + 1] = px[i + 2] = g;
                        hist[g]++;
                    }
                    const total = c.width * c.height;
                    let lo = 0, hi = 255, acc = 0;
                    for (let v = 0; v < 256; v++) { acc += hist[v]; if (acc > total * 0.01) { lo = v; break; } }
                    acc = 0;
                    for (let v = 255; v >= 0; v--) { acc += hist[v]; if (acc > total * 0.01) { hi = v; break; } }
                    if (hi - lo > 40) {
                        const k = 255 / (hi - lo);
                        for (let i = 0; i < px.length; i += 4) {
                            const g = Math.max(0, Math.min(255, (px[i] - lo) * k)) | 0;
                            px[i] = px[i + 1] = px[i + 2] = g;
                        }
                    }
                    ctx.putImageData(d, 0, 0);
                } catch (e) { /* keep the plain picture */ }
                URL.revokeObjectURL(url);
                c.toBlob((blob) => resolve(blob || file), 'image/jpeg', 0.88);
            };
            img.onerror = () => { URL.revokeObjectURL(url); resolve(file); };
            img.src = url;
        });
    }

    function renderTray() {
        const tray = $('scanTray');
        if (!tray) return;
        tray.innerHTML = scans.map((s, i) =>
            '<div class="scan-thumb"><img src="' + s.url + '" alt="page ' + (i + 1) + '"><button type="button" data-rm="' + i + '" title="Remove">✕</button><span>' + (i + 1) + '</span></div>'
        ).join('');
        $('scanSendBtn').classList.toggle('hidden', !scans.length);
        $('scanSendBtn').textContent = 'Send ' + scans.length + ' page' + (scans.length === 1 ? '' : 's') + ' to Axom AI';
        tray.querySelectorAll('[data-rm]').forEach((b) => b.addEventListener('click', () => {
            const s = scans.splice(Number(b.dataset.rm), 1)[0];
            URL.revokeObjectURL(s.url);
            renderTray();
        }));
    }

    async function onScan(ev) {
        const file = ev.target.files && ev.target.files[0];
        ev.target.value = '';
        if (!file) return;
        say('Preparing the page…');
        const blob = await enhance(file);
        scans.push({ blob: blob, url: URL.createObjectURL(blob) });
        say('Page ' + scans.length + ' added. Scan more pages, or send them.');
        renderTray();
    }

    async function sendScans() {
        const files = scans.map((s, i) => new File([s.blob], 'scan-' + (i + 1) + '.jpg', { type: 'image/jpeg' }));
        scans.forEach((s) => URL.revokeObjectURL(s.url));
        scans = [];
        renderTray();
        await send(files);
    }

    // ---------------------------------------------------------------- the list
    function card(u) {
        const st = STATUS[u.status] || [u.status, 'review'];
        const icon = u.kind === 'pdf' ? '📕' : '🖼️';
        const when = (u.created_at || '').replace('T', ' ').slice(0, 16);
        const chips = [];
        if (u.page_count) chips.push('<span class="doc-chip">' + u.page_count + ' page' + (u.page_count === 1 ? '' : 's') + '</span>');
        (u.engines || []).forEach((e) => chips.push('<span class="doc-chip">' + esc(e) + '</span>'));
        if (u.status === 'review' && u.review_pages) chips.push('<span class="doc-chip warn">' + u.review_pages + ' to double-check</span>');
        let note = '';
        if (u.status === 'review') note = '<p class="doc-note">An admin will read and approve it in the admin panel (Document Verify). Nothing reaches Axom AI before that.</p>';
        if (u.status === 'approved') {
            if (u.send_status === 'sent') note = '<p class="doc-note">Approved and sent to Axom AI chat.</p>';
            else if (u.send_status === 'failed') note = '<p class="doc-note err">Approved, but sending to Axom AI chat failed. An admin can send it again from Document Verify.</p>';
            else note = '<p class="doc-note">Approved. Sending to Axom AI chat…</p>';
        }
        if (u.status === 'rejected') note = '<p class="doc-note">The admin did not approve this document.</p>';
        if (u.error) note = '<p class="doc-note err">' + esc(u.error) + '</p>';
        return '<div class="doc-item"><div class="doc-item-top"><span class="doc-item-icon">' + icon + '</span><div style="min-width:0"><div class="doc-item-name">' + esc(u.name) +
            '</div><div class="doc-item-sub">' + esc(when) + (u.uploader ? ' · ' + esc(u.uploader) : '') + '</div></div></div>' +
            '<span class="doc-pill ' + st[1] + '">' + esc(st[0]) + '</span>' +
            (u.status === 'processing' ? '<div class="doc-shimmer"></div>' : '') +
            (chips.length ? '<div class="doc-chips">' + chips.join('') + '</div>' : '') + note + '</div>';
    }

    async function refresh() {
        let data;
        try { data = await api('/api/upload/list'); } catch (e) { return; }
        const rows = data.uploads || [];
        const used = data.pages_used_today || 0, limit = data.daily_limit || 0;
        $('docUsage').textContent = used + ' / ' + limit;
        const fill = $('docMeterFill');
        const pct = limit ? Math.min(100, Math.round(used * 100 / limit)) : 100;
        fill.style.width = pct + '%';
        fill.classList.toggle('high', pct >= 80);
        const ocr = data.ocr || {};
        const warn = [];
        if (!ocr.poppler) warn.push('PDF reading is not installed on the server yet (poppler-utils).');
        if (!ocr.gemini && !ocr.openai) warn.push('No Gemini / OpenAI key found: only Tesseract is available, so results need more checking.');
        $('docWarn').textContent = warn.join(' ');
        $('docWarn').classList.toggle('hidden', !warn.length);
        $('docRows').innerHTML = rows.length ? rows.map(card).join('') : '<p class="doc-empty">Nothing uploaded yet. Your first document will appear here.</p>';
        const busy = rows.some((u) => u.status === 'processing' || u.send_status === 'sending');
        if (busy && !timer) timer = setInterval(refresh, 3000);
        if (!busy && timer) { clearInterval(timer); timer = null; }
    }

    // ---------------------------------------------------------------- start
    document.addEventListener('DOMContentLoaded', () => {
        if (!$('docUploadInput')) return;
        $('docUploadInput').addEventListener('change', (ev) => { const f = Array.from(ev.target.files); ev.target.value = ''; send(f); });
        $('scanInput').addEventListener('change', onScan);
        $('scanSendBtn').addEventListener('click', sendScans);
        $('docRefreshBtn').addEventListener('click', refresh);
        const drop = $('docDrop');
        ['dragenter', 'dragover'].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add('drag'); }));
        ['dragleave', 'drop'].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.remove('drag'); }));
        drop.addEventListener('drop', (ev) => send(Array.from(ev.dataTransfer.files)));
        refresh();
    });
})();
