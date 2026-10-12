// Upload & Scan Documents: files (or phone-camera scans) -> text -> the admin checks it -> Axom AI chat.
(function () {
    const MAX_BYTES = 20 * 1024 * 1024;
    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    let scans = [];          // pictures taken with the camera, not sent yet: [{blob, url}]
    let timer = null;
    let openId = null;

    const STATUS = {
        processing: ['Reading…', 'badge-running'],
        review: ['Needs your review', 'badge-idle'],
        approved: ['Sent to Axom AI', 'badge-completed'],
        rejected: ['Rejected', 'badge-failed'],
        failed: ['Failed', 'badge-failed'],
    };

    function say(msg, bad) {
        const el = $('docMessage');
        if (!el) return;
        el.textContent = msg || '';
        el.style.color = bad ? '#fb7185' : 'var(--text-muted)';
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
            say('Uploaded. The text is being read; it will wait here for your approval.');
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
        say('');
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
    async function refresh() {
        let data;
        try { data = await api('/api/upload/list'); } catch (e) { return; }
        const rows = data.uploads || [];
        $('docUsage').textContent = data.pages_used_today + ' / ' + data.daily_limit + ' pages read by AI today';
        const ocr = data.ocr || {};
        const warn = [];
        if (!ocr.poppler) warn.push('PDF reading is not installed on the server yet (poppler-utils)');
        if (!ocr.gemini && !ocr.openai) warn.push('no Gemini / OpenAI key: only Tesseract is available');
        $('docWarn').textContent = warn.join(' · ');
        $('docWarn').classList.toggle('hidden', !warn.length);
        const body = $('docRows');
        body.innerHTML = rows.length ? rows.map((u) => {
            const st = STATUS[u.status] || [u.status, 'badge-idle'];
            const extra = u.status === 'review' && u.review_pages ? ' · ' + u.review_pages + ' to check' : '';
            return '<tr><td>' + esc(u.name) + '<div class="doc-sub">' + esc((u.created_at || '').replace('T', ' ').slice(0, 16)) + ' · ' + esc(u.uploader) + '</div></td>' +
                '<td>' + (u.page_count || 0) + '</td>' +
                '<td>' + esc((u.engines || []).join(', ')) + '</td>' +
                '<td><span class="badge ' + st[1] + '">' + esc(st[0]) + '</span>' + esc(extra) + (u.error ? '<div class="doc-sub" style="color:#fb7185">' + esc(u.error) + '</div>' : '') + '</td>' +
                '<td class="doc-actions">' +
                (u.status === 'review' || u.status === 'approved' ? '<button class="btn-secondary btn-sm" data-open="' + u.id + '">' + (u.status === 'review' ? 'Review' : 'View') + '</button> ' : '') +
                (u.status !== 'processing' ? '<button class="btn-xs" data-del="' + u.id + '" title="Delete">🗑</button>' : '') + '</td></tr>';
        }).join('') : '<tr><td colspan="5" class="doc-sub" style="padding:14px">Nothing uploaded yet.</td></tr>';
        body.querySelectorAll('[data-open]').forEach((b) => b.addEventListener('click', () => openReview(b.dataset.open)));
        body.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', () => removeUpload(b.dataset.del)));
        const busy = rows.some((u) => u.status === 'processing');
        if (busy && !timer) timer = setInterval(refresh, 3000);
        if (!busy && timer) { clearInterval(timer); timer = null; }
    }

    async function removeUpload(id) {
        if (!confirm('Delete this upload and its text?')) return;
        try { await api('/api/upload/' + id, { method: 'DELETE' }); if (openId === id) closeReview(); await refresh(); } catch (e) { say(e.message, true); }
    }

    // ---------------------------------------------------------------- review
    function closeReview() { openId = null; $('docReview').classList.add('hidden'); $('docReview').innerHTML = ''; }

    async function openReview(id) {
        let m;
        try { m = await api('/api/upload/' + id); } catch (e) { say(e.message, true); return; }
        openId = id;
        const editable = m.status === 'review';
        const box = $('docReview');
        box.classList.remove('hidden');
        box.innerHTML =
            '<div class="doc-review-head"><strong>' + esc(m.name) + '</strong>' +
            '<button class="btn-xs" id="docCloseReview">Close</button></div>' +
            (editable ? '<p class="doc-sub">Read each page, fix mistakes, then approve. Nothing reaches Axom AI before you approve. Pages marked ⚠ need a closer look.</p>' : '') +
            m.pages.map((p) =>
                '<div class="doc-page' + (p.review ? ' doc-flag' : '') + '">' +
                '<div class="doc-page-head">Page ' + p.n + ' · ' + esc(p.engine) + (p.review ? ' · ⚠ ' + esc(p.reason) : '') + (p.edited ? ' · edited' : '') + '</div>' +
                '<div class="doc-page-body">' +
                (p.image ? '<a href="/api/upload/' + id + '/image/' + encodeURIComponent(p.image) + '" target="_blank" rel="noopener"><img class="doc-img" src="/api/upload/' + id + '/image/' + encodeURIComponent(p.image) + '" alt="page ' + p.n + '"></a>' : '') +
                '<textarea class="doc-text" data-page="' + p.n + '" ' + (editable ? '' : 'readonly') + ' spellcheck="false">' + esc(p.text) + '</textarea></div></div>'
            ).join('') +
            (editable ? '<div class="doc-review-actions"><button class="btn btn-primary" id="docApprove" style="width:auto;padding:0 18px">✅ Approve &amp; send to Axom AI</button> <button class="btn-secondary btn-sm" id="docReject">Reject</button></div>' : '') +
            (m.status === 'approved' ? '<div class="doc-review-actions"><button class="btn-secondary btn-sm" id="docResend">Send again</button></div>' : '');
        $('docCloseReview').addEventListener('click', closeReview);
        if (editable) {
            box.querySelectorAll('textarea[data-page]').forEach((t) => {
                const orig = t.value;
                t.addEventListener('change', async () => {
                    try { await api('/api/upload/' + id + '/page/' + t.dataset.page, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: t.value }) }); t.dataset.saved = '1'; say('Saved page ' + t.dataset.page + '.'); }
                    catch (e) { t.value = orig; say(e.message, true); }
                });
            });
            $('docApprove').addEventListener('click', async () => {
                if (!confirm('Send this text to Axom AI chat? The chat will start answering from it.')) return;
                try { await api('/api/upload/' + id + '/approve', { method: 'POST' }); say('Approved. It is being sent to Axom AI.'); closeReview(); refresh(); }
                catch (e) { say(e.message, true); }
            });
            $('docReject').addEventListener('click', async () => {
                try { await api('/api/upload/' + id + '/reject', { method: 'POST' }); closeReview(); refresh(); } catch (e) { say(e.message, true); }
            });
        }
        if (m.status === 'approved' && $('docResend')) {
            $('docResend').addEventListener('click', async () => {
                try { await api('/api/upload/' + id + '/resend', { method: 'POST' }); say('Sending again.'); } catch (e) { say(e.message, true); }
            });
        }
        box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
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
