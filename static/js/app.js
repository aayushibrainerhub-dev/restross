document.addEventListener('DOMContentLoaded', () => {
    
    // Navigation Logic
    const navLinks = document.querySelectorAll('.nav-link');
    const views = document.querySelectorAll('.view');
    
    navLinks.forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            const targetId = link.getAttribute('data-target');
            
            // Update active link
            navLinks.forEach(l => l.classList.remove('active'));
            link.classList.add('active');
            
            // Update active view
            views.forEach(view => {
                view.style.display = view.id === targetId ? 'block' : 'none';
            });
            
            // If switching to restaurants, maybe refresh data
            if (targetId === 'restaurants-view') {
                fetchRestaurants();
            }
        });
    });

    // --- Auth Helpers ---
    function getAuthHeaders() {
        const token = localStorage.getItem('access_token');
        return token ? { 'Authorization': `Bearer ${token}` } : {};
    }

    function handleUnauthorized() {
        localStorage.removeItem('access_token');
        localStorage.removeItem('user');
        window.location.href = '/login';
    }

    // Health Check
    async function checkHealth() {
        const healthStatus = document.getElementById('health-status');
        try {
            const response = await fetch('/health');
            if (response.ok) {
                healthStatus.innerHTML = '<span class="dot online"></span> API Online';
            } else {
                healthStatus.innerHTML = '<span class="dot offline"></span> API Error';
            }
        } catch (error) {
            healthStatus.innerHTML = '<span class="dot offline"></span> API Offline';
        }
    }

    // Fetch Restaurants
    async function fetchRestaurants() {
        const grid = document.getElementById('restaurants-grid');
        grid.innerHTML = '<div class="loading-state">Loading restaurants...</div>';
        
        try {
            // Using correct API prefix matching the FastAPI setup
            const response = await fetch('/api/v1/restaurants', {
                headers: getAuthHeaders()
            });
            if (response.status === 401) { handleUnauthorized(); return; }
            if (!response.ok) throw new Error('Failed to fetch');
            
            const data = await response.json();
            
            if (data.length === 0) {
                grid.innerHTML = '<div class="empty-state"><p>No restaurants found. Add some using the API.</p></div>';
                return;
            }
            
            grid.innerHTML = '';
            data.forEach((restaurant, index) => {
                const card = document.createElement('div');
                card.className = 'card';
                const rating = restaurant.rating_out_of_5 ? `⭐ ${restaurant.rating_out_of_5}` : 'N/A';
                const location = restaurant.location || 'Unknown location';
                const cost = restaurant.approx_cost_for_two_inr ? `₹${restaurant.approx_cost_for_two_inr} for two` : '';
                const price_inr = restaurant.price_inr ? `₹${restaurant.price_inr}` : '';

                card.innerHTML = `
                    <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 0.5rem;">
                        <h3 style="margin: 0;">#${index + 1} ${escapeHtml(restaurant.cafe || 'Unnamed Cafe')}</h3>
                        <span style="background: var(--primary); color: white; padding: 0.2rem 0.5rem; border-radius: 0.25rem; font-weight: bold; font-size: 0.85rem;">${rating}</span>
                    </div>
                    <p style="margin-bottom: 0.5rem; color: var(--text-dark); font-weight: 500;">Menu: ${escapeHtml(restaurant.menu_item || 'Not specified')}</p>
                    <p style="font-size: 0.85rem; color: var(--text-muted);">Feedback: ${escapeHtml(restaurant.feedback || 'No feedback yet')}</p>
                    <div class="meta" style="margin-top: 1rem;">
                        <span>📍 ${escapeHtml(location)}</span>
                        <span>${escapeHtml(price_inr)} | ${escapeHtml(cost)}</span>
                    </div>
                `;
                grid.appendChild(card);
            });
            
        } catch (error) {
            console.error(error);
            grid.innerHTML = '<div class="empty-state" style="color: var(--error)"><p>Error loading restaurants. Make sure the database is running.</p></div>';
        }
    }

    // Utility: Prevent XSS
    function escapeHtml(unsafe) {
        if (!unsafe) return '';
        return unsafe
             .toString()
             .replace(/&/g, "&amp;")
             .replace(/</g, "&lt;")
             .replace(/>/g, "&gt;")
             .replace(/"/g, "&quot;")
             .replace(/'/g, "&#039;");
    }

    // Init
    const refreshBtn = document.getElementById('refresh-restaurants');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', fetchRestaurants);
    }
    
    // Initial fetch
    checkHealth();
    fetchRestaurants();

    // Chat Logic
    const sessionId = Math.random().toString(36).substring(2, 15);
    const chatForm = document.getElementById('chat-form');
    const chatInput = document.getElementById('chat-input');
    const chatMessages = document.getElementById('chat-messages');

    document.querySelectorAll('.starter-prompt').forEach((prompt) => {
        prompt.addEventListener('click', () => {
            chatInput.value = prompt.dataset.prompt;
            chatForm.requestSubmit();
        });
    });

    // --- Delivery Tracking ---
    const DELIVERY_KEY = 'activeDelivery';

    function parseEtaMinutes(text) {
        // Collect ALL minute values mentioned (e.g. "2 minutes ago", "28 minutes")
        // and take the largest — the ETA is always the bigger number.
        const allMatches = [...text.matchAll(/(\d+)\s*[\u2013-]\s*(\d+)\s*min/gi)];
        const rangeValues = allMatches.flatMap(m => [parseInt(m[1], 10), parseInt(m[2], 10)]);

        const singleMatches = [...text.matchAll(/(\d+)\s*min/gi)];
        const singleValues = singleMatches.map(m => parseInt(m[1], 10));

        const all = [...rangeValues, ...singleValues].filter(n => n > 0);
        return all.length > 0 ? Math.max(...all) : null;
    }

    function isOrderConfirmation(text) {
        const lower = text.toLowerCase();
        return (
            (lower.includes('order') && (lower.includes('confirmed') || lower.includes('placed')))
            && lower.includes('min')
        );
    }

    function saveDelivery(etaMinutes) {
        const delivery = {
            placedAt: Date.now(),
            etaMinutes: etaMinutes
        };
        localStorage.setItem(DELIVERY_KEY, JSON.stringify(delivery));
    }

    function clearDelivery() {
        localStorage.removeItem(DELIVERY_KEY);
    }

    function appendDeliveredCard() {
        const card = document.createElement('div');
        card.className = 'message bot delivered-card';
        const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        card.innerHTML = `
            <p>🎉 <strong>Your order has been delivered!</strong> We hope you enjoy your meal. Thank you for ordering with us!</p>
            <span class="msg-time">${now}</span>
        `;
        chatMessages.appendChild(card);
        chatMessages.scrollTop = chatMessages.scrollHeight;
        clearDelivery();
    }

    function checkDeliveryStatus() {
        const raw = localStorage.getItem(DELIVERY_KEY);
        if (!raw) return;
        const delivery = JSON.parse(raw);
        const elapsedMs = Date.now() - delivery.placedAt;
        const elapsedMin = elapsedMs / 60000;
        if (elapsedMin >= delivery.etaMinutes) {
            appendDeliveredCard();
        }
    }

    // Check every 30 seconds
    setInterval(checkDeliveryStatus, 30000);
    // Also check immediately on page load
    checkDeliveryStatus();
    // ---------------------------

    if (chatForm) {
        chatForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const messageText = chatInput.value.trim();
            
            if (messageText) {
                const userMsgEl = document.createElement('div');
                userMsgEl.className = 'message-wrapper user-wrapper';
                const timeString = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
                userMsgEl.innerHTML = `
                    <img src="https://cdn-icons-png.flaticon.com/512/3177/3177440.png" class="avatar" alt="User">
                    <div class="message-content-container">
                        <div class="message-header">
                            <span class="name">You</span>
                            <span class="time">${timeString}</span>
                        </div>
                        <div class="message user">
                            <div class="markdown-body">
                                <p>${escapeHtml(messageText)}</p>
                            </div>
                        </div>
                    </div>
                `;
                chatMessages.appendChild(userMsgEl);
                
                // Clear input
                chatInput.value = '';
                
                // Scroll to bottom
                chatMessages.scrollTop = chatMessages.scrollHeight;

                // Add loading indicator
                const loadingMsgEl = document.createElement('div');
                loadingMsgEl.className = 'message-wrapper bot-wrapper';
                loadingMsgEl.id = 'chat-loading';
                loadingMsgEl.innerHTML = `
                    <img src="https://cdn-icons-png.flaticon.com/512/4712/4712139.png" class="avatar" alt="AI">
                    <div class="message-content-container">
                        <div class="message-header">
                            <span class="name">DesignPilot AI</span>
                            <span class="time">...</span>
                        </div>
                        <div class="message bot">
                            <div class="markdown-body">
                                <p>Thinking & querying database...</p>
                            </div>
                        </div>
                    </div>
                `;
                chatMessages.appendChild(loadingMsgEl);
                chatMessages.scrollTop = chatMessages.scrollHeight;

                try {
                    const response = await fetch('/api/v1/chat', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            ...getAuthHeaders()
                        },
                        body: JSON.stringify({ 
                            message: messageText,
                            session_id: sessionId
                        })
                    });

                    if (response.status === 401) { loadingMsgEl.remove(); handleUnauthorized(); return; }

                    loadingMsgEl.remove();

                    if (!response.ok) {
                        throw new Error(`Server error (${response.status})`);
                    }

                    const data = await response.json();
                    
                    const botMsgEl = document.createElement('div');
                    botMsgEl.className = 'message-wrapper bot-wrapper';
                    const botTimeString = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
                    
                    // Parse Markdown safely
                    const parsedMarkdown = window.marked ? marked.parse(data.response) : `<p>${escapeHtml(data.response).replace(/\n/g, '<br>')}</p>`;

                    botMsgEl.innerHTML = `
                        <img src="https://cdn-icons-png.flaticon.com/512/4712/4712139.png" class="avatar" alt="AI">
                        <div class="message-content-container">
                            <div class="message-header">
                                <span class="name">DesignPilot AI</span>
                                <span class="time">${botTimeString}</span>
                            </div>
                            <div class="message bot">
                                <div class="markdown-body">
                                    ${parsedMarkdown}
                                </div>
                            </div>
                        </div>
                    `;
                    chatMessages.appendChild(botMsgEl);

                    // Show the address picker whenever the backend signals ask_destination
                    if (data.ask_destination) {
                        const hasSaved = data.past_addresses && data.past_addresses.length > 0;
                        const chipsWrapper = document.createElement('div');
                        chipsWrapper.className = 'message-wrapper bot-wrapper address-chips-wrapper';
                        chipsWrapper.innerHTML = `
                            <span style="width:36px;flex-shrink:0;"></span>
                            <div class="address-chips">
                                <span class="chips-label">📍 Choose a delivery address</span>
                                <div class="chips-row" id="address-chips-row">
                                    ${hasSaved ? data.past_addresses.map((addr, i) => `
                                        <button class="address-chip" data-address="${escapeHtml(addr)}" id="chip-addr-${i}">
                                            📌 ${escapeHtml(addr)}
                                        </button>`).join('') : ''}
                                    <button class="address-chip chip-new" id="chip-new">✏️ Enter new address</button>
                                </div>
                                <div class="chips-inline-input" id="chips-new-input-row" style="display:none;">
                                    <input type="text" id="dest-inline-input" placeholder="Type your delivery address…" autocomplete="off" />
                                    <button class="address-chip chip-submit" id="chip-submit-btn">→ Confirm</button>
                                </div>
                            </div>
                        `;
                        chatMessages.appendChild(chipsWrapper);
                        chatMessages.scrollTop = chatMessages.scrollHeight;

                        // Saved address chips → one click submits
                        chipsWrapper.querySelectorAll('.address-chip[data-address]').forEach(btn => {
                            btn.addEventListener('click', () => {
                                chatInput.value = btn.dataset.address;
                                chipsWrapper.remove();
                                chatForm.requestSubmit();
                            });
                        });

                        // "Enter new address" chip → reveal inline input below
                        chipsWrapper.querySelector('#chip-new').addEventListener('click', () => {
                            const inputRow = chipsWrapper.querySelector('#chips-new-input-row');
                            inputRow.style.display = 'flex';
                            chipsWrapper.querySelector('#dest-inline-input').focus();
                        });

                        // Submit inline input
                        function doInlineSubmit() {
                            const val = chipsWrapper.querySelector('#dest-inline-input')?.value?.trim();
                            if (!val) return;
                            chatInput.value = val;
                            chipsWrapper.remove();
                            chatForm.requestSubmit();
                        }
                        chipsWrapper.querySelector('#chip-submit-btn').addEventListener('click', doInlineSubmit);
                        chipsWrapper.querySelector('#dest-inline-input').addEventListener('keydown', e => {
                            if (e.key === 'Enter') { e.preventDefault(); doInlineSubmit(); }
                        });
                    }


                    // If this is an order confirmation, start delivery countdown
                    if (isOrderConfirmation(data.response)) {
                        const eta = parseEtaMinutes(data.response);
                        if (eta) {
                            saveDelivery(eta);
                        }
                    }
                } catch (err) {
                    loadingMsgEl.remove();
                    const errorMsgEl = document.createElement('div');
                    errorMsgEl.className = 'message-wrapper bot-wrapper';
                    errorMsgEl.innerHTML = `
                        <img src="https://cdn-icons-png.flaticon.com/512/4712/4712139.png" class="avatar" alt="AI">
                        <div class="message-content-container">
                            <div class="message-header">
                                <span class="name">DesignPilot AI</span>
                                <span class="time">Error</span>
                            </div>
                            <div class="message bot" style="color: var(--error);">
                                <div class="markdown-body">
                                    <p>Sorry, I couldn't process your request: ${escapeHtml(err.message)}</p>
                                </div>
                            </div>
                        </div>
                    `;
                    chatMessages.appendChild(errorMsgEl);
                }

                chatMessages.scrollTop = chatMessages.scrollHeight;
            }
        });
    }
});
