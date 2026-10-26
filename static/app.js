let currentJobId = null;
let pollInterval = null;

// Pagination & Filtering state for Crawled Datasets
let allJobs = [];
let currentJobsPage = 1;
const JOBS_PAGE_SIZE = 10;
let jobsSearchQuery = '';

document.addEventListener('DOMContentLoaded', () => {
    initApp();
});

function initApp() {
    const crawlForm = document.getElementById('crawlForm');
    const refreshJobsBtn = document.getElementById('refreshJobsBtn');
    const clearLogsBtn = document.getElementById('clearLogsBtn');
    const closeModalBtn = document.getElementById('closeModalBtn');
    const modalDismissBtn = document.getElementById('modalDismissBtn');
    const chatForm = document.getElementById('chatForm');
    const apiKeyToggleBtn = document.getElementById('apiKeyToggleBtn');
    const saveApiKeyBtn = document.getElementById('saveApiKeyBtn');
    const stopCrawlBtn = document.getElementById('stopCrawlBtn');
    const resetVectorBtn = document.getElementById('resetVectorBtn');
    const jobsSearchInput = document.getElementById('jobsSearchInput');
    const clearJobsSearchBtn = document.getElementById('clearJobsSearchBtn');

    crawlForm.addEventListener('submit', handleStartCrawl);
    refreshJobsBtn.addEventListener('click', () => loadJobs());
    clearLogsBtn.addEventListener('click', () => {
        document.getElementById('logTerminal').innerHTML = '<p class="log-placeholder">Logs cleared.</p>';
    });

    if (jobsSearchInput) {
        jobsSearchInput.addEventListener('input', (e) => {
            jobsSearchQuery = e.target.value.trim().toLowerCase();
            currentJobsPage = 1; // Reset to page 1 on new search
            if (clearJobsSearchBtn) {
                if (jobsSearchQuery.length > 0) {
                    clearJobsSearchBtn.classList.remove('hidden');
                } else {
                    clearJobsSearchBtn.classList.add('hidden');
                }
            }
            renderJobsTable();
        });
    }

    if (clearJobsSearchBtn) {
        clearJobsSearchBtn.addEventListener('click', () => {
            if (jobsSearchInput) jobsSearchInput.value = '';
            jobsSearchQuery = '';
            clearJobsSearchBtn.classList.add('hidden');
            currentJobsPage = 1;
            renderJobsTable();
        });
    }

    if (stopCrawlBtn) {
        stopCrawlBtn.addEventListener('click', stopActiveCrawl);
    }

    if (resetVectorBtn) {
        resetVectorBtn.addEventListener('click', resetVectorDb);
    }

    closeModalBtn.addEventListener('click', closeModal);
    modalDismissBtn.addEventListener('click', closeModal);

    if (chatForm) {
        chatForm.addEventListener('submit', handleChatSubmit);
    }

    // API Key config
    if (apiKeyToggleBtn) {
        apiKeyToggleBtn.addEventListener('click', () => {
            const drawer = document.getElementById('apiKeyDrawer');
            drawer.classList.toggle('hidden');
        });
    }

    if (saveApiKeyBtn) {
        saveApiKeyBtn.addEventListener('click', () => {
            const key = document.getElementById('geminiApiKeyInput').value.trim();
            localStorage.setItem('axomai_gemini_key', key);
            alert(key ? 'Gemini API Key saved locally!' : 'API Key cleared.');
            document.getElementById('apiKeyDrawer').classList.add('hidden');
        });
    }

    // Restore saved API key
    const savedKey = localStorage.getItem('axomai_gemini_key');
    if (savedKey) {
        const input = document.getElementById('geminiApiKeyInput');
        if (input) input.value = savedKey;
    }

    const refreshScheduleBtn = document.getElementById('refreshScheduleBtn');
    if (refreshScheduleBtn) {
        refreshScheduleBtn.addEventListener('click', loadSchedules);
    }

    // Initial load
    loadJobs();
    loadRagStats();
    loadSchedules();
}

async function handleStartCrawl(e) {
    e.preventDefault();

    const seedUrl = document.getElementById('seedUrl').value.trim();
    const maxPages = parseInt(document.getElementById('maxPages').value, 10);
    const maxDepth = parseInt(document.getElementById('maxDepth').value, 10);
    const crawlDelay = parseFloat(document.getElementById('crawlDelay').value);
    const renderJs = document.getElementById('renderJs').checked;
    const forceRecrawl = document.getElementById("forceRecrawl") ? document.getElementById("forceRecrawl").checked : false;
    const includeSubdomains = document.getElementById("includeSubdomains") ? document.getElementById("includeSubdomains").checked : false;

    if (!seedUrl) return;

    const startBtn = document.getElementById('startBtn');
    startBtn.disabled = true;
    startBtn.querySelector('.btn-text').textContent = "Launching Crawler...";

    // Reset counters
    if (document.getElementById('counterNew')) {
        document.getElementById('counterNew').textContent = '0';
        document.getElementById('counterUpdated').textContent = '0';
        document.getElementById('counterSkipped').textContent = '0';
    }

    try {
        const response = await fetch('/api/crawl/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                url: seedUrl,
                max_pages: maxPages,
                max_depth: maxDepth,
                crawl_delay: crawlDelay,
                render_js: renderJs,
                force_recrawl: forceRecrawl,
                include_subdomains: includeSubdomains
            })
        });

        if (!response.ok) {
            const err = await response.json();
            alert(`Error: ${err.detail || 'Could not start crawler'}`);
            resetButton();
            return;
        }

        const data = await response.json();
        currentJobId = data.job_id;

        // Show Stop Button
        const stopBtn = document.getElementById('stopCrawlBtn');
        if (stopBtn) {
            stopBtn.classList.remove('hidden');
            stopBtn.disabled = false;
            stopBtn.textContent = '🛑 Stop Crawl';
        }

        // Update UI
        updateBadge('running', 'Crawling');
        document.getElementById('logTerminal').innerHTML = '';
        const modeLabel = forceRecrawl ? "Force Re-crawl" : "Smart Skip Active";
        appendLog(`🚀 Crawl initiated for ${seedUrl} [${modeLabel}, Job ID: ${currentJobId}]`);

        // Start polling
        if (pollInterval) clearInterval(pollInterval);
        pollInterval = setInterval(() => pollJobStatus(currentJobId), 1200);

        // Refresh table to show active job
        loadJobs();

    } catch (err) {
        console.error('Request failed:', err);
        alert('Failed to connect to crawler API server.');
        resetButton();
    }
}

async function pollJobStatus(jobId) {
    try {
        const res = await fetch(`/api/crawl/status/${jobId}`);
        if (!res.ok) return;

        const data = await res.json();
        
        // Update Progress Bar
        const crawled = data.pages_crawled || 0;
        const max = data.max_pages || 1;
        const percent = Math.min(100, Math.round((crawled / max) * 100));

        document.getElementById('progressText').textContent = `${crawled} / ${max} pages`;
        document.getElementById('progressPercent').textContent = `${percent}%`;
        document.getElementById('progressBar').style.width = `${percent}%`;

        // Update Change Counters
        if (document.getElementById('counterNew')) {
            document.getElementById('counterNew').textContent = data.pages_new || 0;
            document.getElementById('counterUpdated').textContent = data.pages_updated || 0;
            document.getElementById('counterSkipped').textContent = data.pages_skipped || 0;
        }

        // Current URL
        document.getElementById('currentUrlDisplay').textContent = data.current_url || 'Wrapping up crawl...';

        // Update logs
        if (data.latest_logs && data.latest_logs.length > 0) {
            renderLogs(data.latest_logs);
        }

        // Check if finished or stopped
        if (data.status === 'completed' || data.status === 'failed' || data.status === 'stopped') {
            clearInterval(pollInterval);
            pollInterval = null;

            if (data.status === 'completed') {
                updateBadge('completed', 'Finished');
                document.getElementById('progressBar').style.width = '100%';
                document.getElementById('progressPercent').textContent = '100%';
                appendLog(`✅ Crawl finished! Total: ${data.pages_crawled} (New: ${data.pages_new || 0}, Updated: ${data.pages_updated || 0}, Skipped: ${data.pages_skipped || 0})`);
            } else if (data.status === 'stopped') {
                updateBadge('stopped', 'Stopped');
                appendLog(`🛑 Crawl stopped by user! Saved ${data.pages_crawled} pages (New: ${data.pages_new || 0}, Updated: ${data.pages_updated || 0}, Skipped: ${data.pages_skipped || 0})`);
            } else {
                updateBadge('failed', 'Failed');
                appendLog(`❌ Crawl terminated with errors.`);
            }

            const stopBtn = document.getElementById('stopCrawlBtn');
            if (stopBtn) stopBtn.classList.add('hidden');

            resetButton();
            loadJobs();
            loadStats();
        }

    } catch (err) {
        console.error('Status poll error:', err);
    }
}


function renderLogs(logs) {
    const terminal = document.getElementById('logTerminal');
    terminal.innerHTML = '';
    logs.forEach(msg => {
        const p = document.createElement('p');
        p.className = 'log-entry';
        p.textContent = msg;
        terminal.appendChild(p);
    });
    terminal.scrollTop = terminal.scrollHeight;
}

function appendLog(msg) {
    const terminal = document.getElementById('logTerminal');
    const p = document.createElement('p');
    p.className = 'log-entry';
    p.textContent = msg;
    terminal.appendChild(p);
    terminal.scrollTop = terminal.scrollHeight;
}

function updateBadge(type, label) {
    const badge = document.getElementById('liveStatusBadge');
    badge.className = `badge badge-${type}`;
    badge.textContent = label;
}

function resetButton() {
    const startBtn = document.getElementById('startBtn');
    startBtn.disabled = false;
    startBtn.querySelector('.btn-text').textContent = "Start Web Crawler";
}

async function loadJobs() {
    try {
        const res = await fetch('/api/crawl/jobs');
        if (!res.ok) return;

        const data = await res.json();
        allJobs = data.jobs || [];

        // Update metric counters from full dataset
        const totalJobsEl = document.getElementById('statTotalJobs');
        if (totalJobsEl) totalJobsEl.textContent = allJobs.length;

        const totalPages = allJobs.reduce((sum, j) => sum + (j.total_pages || 0), 0);
        const totalPagesEl = document.getElementById('statTotalPages');
        if (totalPagesEl) totalPagesEl.textContent = totalPages;

        const activeCount = allJobs.filter(j => j.status === 'running' || j.status === 'queued').length;
        const activeJobsEl = document.getElementById('statActiveJobs');
        if (activeJobsEl) activeJobsEl.textContent = activeCount;

        renderJobsTable();
    } catch (err) {
        console.error('Failed to load jobs:', err);
    }
}

function renderJobsTable() {
    const tbody = document.getElementById('jobsTableBody');
    const paginationInfo = document.getElementById('jobsPaginationInfo');
    const paginationControls = document.getElementById('jobsPaginationControls');
    const paginationBar = document.getElementById('jobsPagination');
    if (!tbody) return;

    // Filter jobs based on search query
    let filtered = allJobs;
    if (jobsSearchQuery) {
        filtered = allJobs.filter(job => {
            const jId = (job.job_id || '').toLowerCase();
            const seed = (job.seed_url || '').toLowerCase();
            const dom = extractDomain(job.seed_url || '').toLowerCase();
            return jId.includes(jobsSearchQuery) || seed.includes(jobsSearchQuery) || dom.includes(jobsSearchQuery);
        });
    }

    if (filtered.length === 0) {
        if (jobsSearchQuery) {
            tbody.innerHTML = `<tr><td colspan="7" class="empty-state">No datasets found matching "<strong>${escapeHtml(jobsSearchQuery)}</strong>". Try a different search term.</td></tr>`;
        } else {
            tbody.innerHTML = '<tr><td colspan="7" class="empty-state">No crawled datasets found yet. Start your first crawl above!</td></tr>';
        }
        if (paginationInfo) paginationInfo.textContent = 'Showing 0 of 0 datasets';
        if (paginationControls) paginationControls.innerHTML = '';
        if (paginationBar) paginationBar.classList.add('hidden');
        return;
    }

    if (paginationBar) paginationBar.classList.remove('hidden');

    const totalPages = Math.max(1, Math.ceil(filtered.length / JOBS_PAGE_SIZE));
    if (currentJobsPage > totalPages) currentJobsPage = totalPages;
    if (currentJobsPage < 1) currentJobsPage = 1;

    const startIdx = (currentJobsPage - 1) * JOBS_PAGE_SIZE;
    const endIdx = Math.min(startIdx + JOBS_PAGE_SIZE, filtered.length);
    const pagedJobs = filtered.slice(startIdx, endIdx);

    // Render table rows (latest 10 per page)
    tbody.innerHTML = pagedJobs.map(job => {
        const domain = extractDomain(job.seed_url || '');
        const badgeClass = job.status === 'completed' ? 'badge-completed' : (job.status === 'running' ? 'badge-running' : 'badge-failed');
        const sizeKb = job.file_size_bytes ? (job.file_size_bytes / 1024).toFixed(1) + ' KB' : '—';
        const dateStr = job.completed_at ? new Date(job.completed_at).toLocaleString() : 'In Progress';

        return `
            <tr>
                <td><strong>${escapeHtml(job.job_id)}</strong></td>
                <td title="${escapeHtml(job.seed_url)}">${escapeHtml(domain)}</td>
                <td><span class="badge ${badgeClass}">${escapeHtml(job.status)}</span></td>
                <td>
                    <strong>${job.total_pages || 0}</strong>
                    <div style="font-size:0.72rem; color:var(--text-muted); margin-top:2px;">
                        <span style="color:#059669; font-weight:600;">+${job.pages_new ?? job.total_pages ?? 0}</span>
                        ${job.pages_skipped ? `<span style="color:#64748b;"> | ⏩ ${job.pages_skipped}</span>` : ''}
                        ${job.pages_updated ? `<span style="color:#d97706;"> | 🔄 ${job.pages_updated}</span>` : ''}
                    </div>
                </td>
                <td>${dateStr}</td>
                <td>${sizeKb}</td>
                <td>
                    <div class="table-actions">
                        <button class="btn btn-emerald btn-xs" onclick="indexDataset('${escapeHtml(job.job_id)}')" title="Chunk and store into Vector DB">⚡ Index</button>
                        <button class="btn btn-secondary btn-xs" onclick="inspectDataset('${escapeHtml(job.job_id)}')">👁 View</button>
                        <a href="/api/crawl/export/${job.job_id}/json" class="btn btn-primary btn-xs" target="_blank" title="Download JSON (page name + content only)">⬇ JSON</a>
                        <a href="/api/crawl/export/${job.job_id}/csv" class="btn-csv" target="_blank" title="Download Excel CSV (page name + content only)">📊 CSV</a>
                        <a href="/api/crawl/export/${job.job_id}/pdf" class="btn-pdf" target="_blank" title="Download PDF (page name + content only)">📑 PDF</a>
                        <button class="btn-delete" onclick="deleteDataset('${escapeHtml(job.job_id)}')" title="Delete Dataset & Vectors">🗑️</button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');

    // Update pagination info text
    if (paginationInfo) {
        paginationInfo.innerHTML = `Showing <strong>${startIdx + 1}–${endIdx}</strong> of <strong>${filtered.length}</strong> datasets${jobsSearchQuery ? ' (filtered)' : ''}`;
    }

    // Render pagination controls (Step-by-step buttons)
    renderPaginationControls(totalPages, filtered.length);
}

function renderPaginationControls(totalPages, totalItems) {
    const controls = document.getElementById('jobsPaginationControls');
    if (!controls) return;

    if (totalPages <= 1) {
        controls.innerHTML = '';
        return;
    }

    let html = '';

    // Prev Button
    const prevDisabled = currentJobsPage <= 1 ? 'disabled' : '';
    html += `<button class="page-btn page-nav" ${prevDisabled} onclick="goToJobsPage(${currentJobsPage - 1})" title="Previous Page">← Prev</button>`;

    // Page number buttons with smart windowing
    const pages = getPaginationPageNumbers(currentJobsPage, totalPages);
    pages.forEach(p => {
        if (p === '...') {
            html += `<span class="page-ellipsis">…</span>`;
        } else {
            const activeClass = p === currentJobsPage ? 'active' : '';
            html += `<button class="page-btn ${activeClass}" onclick="goToJobsPage(${p})">${p}</button>`;
        }
    });

    // Next Button
    const nextDisabled = currentJobsPage >= totalPages ? 'disabled' : '';
    html += `<button class="page-btn page-nav" ${nextDisabled} onclick="goToJobsPage(${currentJobsPage + 1})" title="Next Page">Next →</button>`;

    controls.innerHTML = html;
}

function getPaginationPageNumbers(current, total) {
    if (total <= 7) {
        const pages = [];
        for (let i = 1; i <= total; i++) pages.push(i);
        return pages;
    }

    const pages = [1];
    if (current > 3) {
        pages.push('...');
    }

    const start = Math.max(2, current - 1);
    const end = Math.min(total - 1, current + 1);

    for (let i = start; i <= end; i++) {
        if (!pages.includes(i)) pages.push(i);
    }

    if (current < total - 2) {
        pages.push('...');
    }

    if (!pages.includes(total)) {
        pages.push(total);
    }

    return pages;
}

function goToJobsPage(pageNum) {
    currentJobsPage = pageNum;
    renderJobsTable();
}
window.goToJobsPage = goToJobsPage;

function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function extractDomain(url) {
    try {
        const parsed = new URL(url);
        return parsed.hostname;
    } catch {
        return url || 'Unknown';
    }
}

async function inspectDataset(jobId) {
    const modal = document.getElementById('previewModal');
    const content = document.getElementById('modalJsonContent');
    const title = document.getElementById('modalTitle');
    const downloadBtn = document.getElementById('modalDownloadBtn');
    const downloadCsvBtn = document.getElementById('modalDownloadCsvBtn');
    const downloadPdfBtn = document.getElementById('modalDownloadPdfBtn');

    title.textContent = `Dataset Inspector: ${jobId}`;
    content.textContent = 'Loading dataset...';
    downloadBtn.href = `/api/crawl/export/${jobId}/json`;
    if (downloadCsvBtn) {
        downloadCsvBtn.href = `/api/crawl/export/${jobId}/csv`;
    }
    if (downloadPdfBtn) {
        downloadPdfBtn.href = `/api/crawl/export/${jobId}/pdf`;
    }
    modal.classList.remove('hidden');

    try {
        const res = await fetch(`/api/crawl/view/${jobId}`);
        if (!res.ok) {
            content.textContent = 'Could not retrieve dataset details.';
            return;
        }
        const data = await res.json();
        content.textContent = JSON.stringify(data, null, 2);
    } catch (err) {
        content.textContent = 'Error displaying dataset.';
    }
}

function closeModal() {
    document.getElementById('previewModal').classList.add('hidden');
}

async function stopActiveCrawl() {
    if (!currentJobId) return;
    const stopBtn = document.getElementById('stopCrawlBtn');
    if (stopBtn) {
        stopBtn.disabled = true;
        stopBtn.textContent = 'Stopping...';
    }
    try {
        const res = await fetch(`/api/crawl/stop/${currentJobId}`, { method: 'POST' });
        if (res.ok) {
            appendLog('🛑 Stop signal acknowledged by crawler.');
        }
    } catch (err) {
        console.error('Stop request error:', err);
    }
}

async function deleteDataset(jobId) {
    if (!confirm(`Are you sure you want to delete dataset '${jobId}'? This will permanently remove the file and all associated vector embeddings.`)) {
        return;
    }

    try {
        const res = await fetch(`/api/crawl/job/${jobId}`, { method: 'DELETE' });
        if (res.ok) {
            const data = await res.json();
            appendLog(`🗑️ Dataset ${jobId} deleted (${data.deleted_vectors || 0} vectors removed).`);
            loadJobs();
            loadRagStats();
        } else {
            const errData = await res.json().catch(() => ({}));
            alert(`Failed to delete dataset: ${errData.detail || 'Server error'}`);
        }
    } catch (err) {
        console.error('Delete error:', err);
        alert('Network error deleting dataset.');
    }
}

async function resetVectorDb() {
    if (!confirm('Are you sure you want to RESET the entire ChromaDB Vector Store? All indexed vectors will be deleted. You can re-index your datasets at any time.')) {
        return;
    }

    try {
        const res = await fetch('/api/rag/reset', { method: 'POST' });
        if (res.ok) {
            const data = await res.json();
            alert(`✅ Vector DB reset successfully! Deleted ${data.deleted_vectors || 0} vectors.`);
            loadRagStats();
        } else {
            alert('Failed to reset vector database.');
        }
    } catch (err) {
        console.error('Reset vector DB error:', err);
        alert('Error connecting to server.');
    }
}

// --- RAG Functions ---

async function indexDataset(jobId) {
    const btn = event.target;
    const origText = btn.textContent;
    btn.disabled = true;
    btn.textContent = "⏳ Indexing...";

    try {
        const res = await fetch(`/api/rag/index/${jobId}`, {
            method: 'POST'
        });

        if (!res.ok) {
            const err = await res.json();
            alert(`Indexing failed: ${err.detail || 'Unknown error'}`);
            btn.disabled = false;
            btn.textContent = origText;
            return;
        }

        const data = await res.json();
        alert(`Success! ${data.message} (Total vectors in DB: ${data.total_vectors_in_db})`);
        btn.textContent = "✅ Indexed";
        loadRagStats();
    } catch (err) {
        console.error('Index error:', err);
        alert('Failed to connect to RAG indexer.');
        btn.disabled = false;
        btn.textContent = origText;
    }
}

async function loadRagStats() {
    try {
        const res = await fetch('/api/rag/stats');
        if (!res.ok) return;
        const data = await res.json();
        const countElem = document.getElementById('statIndexedVectors');
        if (countElem) {
            countElem.textContent = data.total_vectors || 0;
        }
    } catch (err) {
        console.error('Failed to load RAG stats:', err);
    }
}

// --- GENERATIVE AI CHAT LOGIC ---

async function handleChatSubmit(e) {
    e.preventDefault();
    const chatInput = document.getElementById('chatInput');
    const question = chatInput.value.trim();
    if (!question) return;

    chatInput.value = '';
    const messagesContainer = document.getElementById('chatMessages');

    // 1. Append User Message
    appendChatMessage('user', question);

    // 2. Append Typing Indicator
    const typingId = 'typing_' + Date.now();
    appendTypingIndicator(typingId);

    // Get selected provider & API key
    const provider = document.getElementById('llmProviderSelect').value;
    const apiKey = localStorage.getItem('axomai_gemini_key') || null;

    try {
        const res = await fetch('/api/rag/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                question: question,
                top_k: 4,
                provider: provider,
                api_key: apiKey
            })
        });

        removeTypingIndicator(typingId);

        if (!res.ok) {
            appendChatMessage('bot', '⚠️ Error generating answer. Please make sure you have indexed at least one dataset below.');
            return;
        }

        const data = await res.json();
        appendChatMessage('bot', data.answer, data.sources, data.provider_used);

    } catch (err) {
        console.error('Chat error:', err);
        removeTypingIndicator(typingId);
        appendChatMessage('bot', '⚠️ Connection error. Could not reach the Axom AI server.');
    }
}

function appendChatMessage(sender, text, sources = [], provider = '') {
    const messagesContainer = document.getElementById('chatMessages');
    const msgDiv = document.createElement('div');
    msgDiv.className = `chat-message ${sender}`;

    const avatar = sender === 'user' ? '👤' : '⚡';

    let formattedText = formatSimpleMarkdown(text);

    let sourcesHtml = '';
    if (sources && sources.length > 0) {
        const pills = sources.map(s => `
            <a href="${escapeHtml(s.url)}" target="_blank" class="source-pill" title="Relevance: ${Math.round(s.similarity * 100)}%">
                🔗 ${escapeHtml(s.title || s.url)}
            </a>
        `).join('');
        sourcesHtml = `
            <div class="msg-sources">
                <span class="source-label">Sources:</span>
                ${pills}
            </div>
        `;
    }

    msgDiv.innerHTML = `
        <div class="msg-avatar">${avatar}</div>
        <div class="msg-content">
            <div>${formattedText}</div>
            ${sourcesHtml}
        </div>
    `;

    messagesContainer.appendChild(msgDiv);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

function appendTypingIndicator(id) {
    const messagesContainer = document.getElementById('chatMessages');
    const typingDiv = document.createElement('div');
    typingDiv.id = id;
    typingDiv.className = 'chat-message bot';
    typingDiv.innerHTML = `
        <div class="msg-avatar">⚡</div>
        <div class="msg-content">
            <div class="typing-dots">
                <span class="typing-dot"></span>
                <span class="typing-dot"></span>
                <span class="typing-dot"></span>
            </div>
        </div>
    `;
    messagesContainer.appendChild(typingDiv);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

function removeTypingIndicator(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
}

function formatSimpleMarkdown(text) {
    if (!text) return '';
    let escaped = escapeHtml(text);
    // Bold
    escaped = escaped.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    // Blockquote
    escaped = escaped.replace(/^&gt; (.*$)/gm, '<blockquote style="border-left: 2px solid var(--accent-cyan); padding-left: 8px; margin: 6px 0; color: #94a3b8;">$1</blockquote>');
    // Line breaks
    escaped = escaped.replace(/\n/g, '<br>');
    return escaped;
}

function escapeHtml(text) {
    if (!text) return '';
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };
    return text.replace(/[&<>"']/g, m => map[m]);
}

// --- Auto-Sync Schedule ---

async function loadSchedules() {
    try {
        const res = await fetch('/api/schedule/list');
        if (!res.ok) return;
        const data = await res.json();
        const schedules = data.schedules || [];

        const tbody = document.getElementById('scheduleTableBody');
        if (!tbody) return;

        if (schedules.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" class="empty-state">No scheduled sites yet. Crawl a site to auto-register it.</td></tr>';
            return;
        }

        tbody.innerHTML = schedules.map(s => {
            const domain = extractDomain(s.seed_url || '');
            const lastCrawled = s.last_crawled ? timeAgo(s.last_crawled) : '—';
            const nextRun = s.next_run ? new Date(s.next_run + 'Z').toLocaleDateString() : '—';
            const daysLeft = s.days_remaining != null ? s.days_remaining + 'd' : '—';
            const runs = s.total_auto_runs || 0;
            const lastStatus = s.last_auto_status || '—';
            const statusClass = lastStatus === 'completed' ? 'badge-completed' : (lastStatus === 'failed' ? 'badge-failed' : '');
            const checked = s.enabled ? 'checked' : '';
            const encodedUrl = encodeURIComponent(s.seed_url);

            return `
                <tr>
                    <td title="${escapeHtml(s.seed_url)}"><strong>${escapeHtml(domain)}</strong></td>
                    <td>${lastCrawled}</td>
                    <td>${nextRun}</td>
                    <td><strong>${daysLeft}</strong></td>
                    <td>${runs}</td>
                    <td><span class="badge ${statusClass}">${lastStatus}</span></td>
                    <td>
                        <label class="switch switch-sm">
                            <input type="checkbox" ${checked} onchange="toggleSchedule('${encodedUrl}', this.checked)">
                            <span class="slider round"></span>
                        </label>
                    </td>
                </tr>
            `;
        }).join('');
    } catch (err) {
        console.error('Failed to load schedules:', err);
    }
}

async function toggleSchedule(encodedUrl, enabled) {
    const seedUrl = decodeURIComponent(encodedUrl);
    try {
        await fetch('/api/schedule/toggle', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ seed_url: seedUrl, enabled: enabled })
        });
    } catch (err) {
        console.error('Toggle schedule error:', err);
    }
}

function timeAgo(isoStr) {
    try {
        const date = new Date(isoStr + 'Z');
        const now = new Date();
        const diffMs = now - date;
        const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
        const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
        if (diffDays > 0) return diffDays + 'd ago';
        if (diffHours > 0) return diffHours + 'h ago';
        return 'just now';
    } catch {
        return '—';
    }
}
