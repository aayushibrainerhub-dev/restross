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
            const response = await fetch('/api/v1/restaurants');
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
                userMsgEl.className = 'message user';
                const timeString = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
                userMsgEl.innerHTML = `<p>${escapeHtml(messageText)}</p><span class="msg-time">${timeString}</span>`;
                chatMessages.appendChild(userMsgEl);
                
                // Clear input
                chatInput.value = '';
                
                // Scroll to bottom
                chatMessages.scrollTop = chatMessages.scrollHeight;

                // Add loading indicator
                const loadingMsgEl = document.createElement('div');
                loadingMsgEl.className = 'message bot';
                loadingMsgEl.id = 'chat-loading';
                loadingMsgEl.textContent = "Thinking & querying database...";
                chatMessages.appendChild(loadingMsgEl);
                chatMessages.scrollTop = chatMessages.scrollHeight;

                try {
                    const response = await fetch('/api/v1/chat', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ 
                            message: messageText,
                            session_id: sessionId
                        })
                    });

                    loadingMsgEl.remove();

                    if (!response.ok) {
                        throw new Error(`Server error (${response.status})`);
                    }

                    const data = await response.json();
                    
                    const botMsgEl = document.createElement('div');
                    botMsgEl.className = 'message bot';
                    const botTimeString = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
                    botMsgEl.innerHTML = `<p>${escapeHtml(data.response).replace(/\\n/g, '<br>')}</p><span class="msg-time">${botTimeString}</span>`;
                    chatMessages.appendChild(botMsgEl);

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
                    errorMsgEl.className = 'message bot';
                    errorMsgEl.style.color = 'var(--error)';
                    errorMsgEl.textContent = `Sorry, I couldn't process your request: ${err.message}`;
                    chatMessages.appendChild(errorMsgEl);
                }

                chatMessages.scrollTop = chatMessages.scrollHeight;
            }
        });
    }
});
