/**
 * GrowAssistant Bridge - Core Application JavaScript
 * Provides shared utilities, API helpers, and common functionality
 */

// ============================================
// API Helper Module
// ============================================
const API = {
    /**
     * Make a GET request to an API endpoint
     * @param {string} endpoint - The API endpoint
     * @returns {Promise<any>} - The JSON response
     */
    async get(endpoint) {
        try {
            const response = await fetch(endpoint);
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            return await response.json();
        } catch (error) {
            console.error(`API GET ${endpoint} failed:`, error);
            throw error;
        }
    },

    /**
     * Make a POST request to an API endpoint
     * @param {string} endpoint - The API endpoint
     * @param {object} data - The data to send
     * @returns {Promise<any>} - The JSON response
     */
    async post(endpoint, data = {}) {
        try {
            const response = await fetch(endpoint, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(data),
            });
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            return await response.json();
        } catch (error) {
            console.error(`API POST ${endpoint} failed:`, error);
            throw error;
        }
    }
};

// ============================================
// Utility Functions
// ============================================
const Utils = {
    /**
     * Escape HTML to prevent XSS
     * @param {string} text - The text to escape
     * @returns {string} - The escaped text
     */
    escapeHtml(text) {
        if (text === null || text === undefined) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    },

    /**
     * Format a Unix timestamp to a human-readable string
     * @param {number} timestamp - The Unix timestamp
     * @returns {string} - The formatted date/time
     */
    formatTimestamp(timestamp) {
        if (!timestamp) return 'N/A';
        const date = new Date(timestamp * 1000);
        return date.toLocaleString('en-US', {
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit'
        });
    },

    /**
     * Format a relative time (e.g., "2 min ago")
     * @param {number} timestamp - The Unix timestamp
     * @returns {string} - The relative time string
     */
    formatRelativeTime(timestamp) {
        if (!timestamp) return 'N/A';
        const now = Date.now() / 1000;
        const diff = now - timestamp;

        if (diff < 60) return 'Just now';
        if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
        if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
        return `${Math.floor(diff / 86400)}d ago`;
    },

    /**
     * Debounce a function
     * @param {Function} func - The function to debounce
     * @param {number} wait - The debounce delay in ms
     * @returns {Function} - The debounced function
     */
    debounce(func, wait) {
        let timeout;
        return function executedFunction(...args) {
            const later = () => {
                clearTimeout(timeout);
                func(...args);
            };
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
        };
    },

    /**
     * Generate a unique ID
     * @returns {string} - A unique ID
     */
    generateId() {
        return `id-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
    }
};

// ============================================
// Connection Status Management
// ============================================
const ConnectionManager = {
    status: null,
    listeners: [],

    /**
     * Add a listener for connection status changes
     * @param {Function} callback - The callback function
     */
    addListener(callback) {
        this.listeners.push(callback);
    },

    /**
     * Update the connection status
     * @param {object} status - The new status object
     */
    updateStatus(status) {
        this.status = status;
        this.listeners.forEach(callback => callback(status));
        this.updateUI(status);
    },

    /**
     * Update the UI based on connection status
     * @param {object} status - The status object
     */
    updateUI(status) {
        const headerBadge = document.getElementById('header-status-badge');
        const headerStatusText = document.getElementById('header-status-text');
        const headerAlert = document.getElementById('header-registration-alert');

        if (!status) return;

        // Determine connection state
        const isConnected = status.status === 'ready';
        const isConnecting = ['initializing', 'busy', 'connected'].includes(status.status);
        const needsRegistration = status.status === 'registration';
        const hasError = status.status === 'error';

        // Update header badge
        if (headerBadge) {
            const badge = headerBadge.querySelector('.badge');
            if (badge) {
                badge.classList.remove('badge-success', 'badge-warning', 'badge-error', 'badge-info');
                if (isConnected) {
                    badge.classList.add('badge-success');
                } else if (hasError) {
                    badge.classList.add('badge-error');
                } else if (needsRegistration) {
                    badge.classList.add('badge-warning');
                } else {
                    badge.classList.add('badge-info');
                }
            }
            if (headerStatusText) {
                if (isConnected) {
                    headerStatusText.textContent = 'Connected';
                } else if (hasError) {
                    headerStatusText.textContent = 'Error';
                } else if (needsRegistration) {
                    headerStatusText.textContent = 'Register';
                } else {
                    headerStatusText.textContent = 'Connecting';
                }
            }
            headerBadge.classList.remove('hidden');
            headerBadge.classList.add('flex');
        }

        // Show/hide registration alert
        if (headerAlert) {
            if (needsRegistration) {
                headerAlert.classList.remove('hidden');
            } else {
                headerAlert.classList.add('hidden');
            }
        }
    },

    /**
     * Fetch the current connection status
     */
    async fetch() {
        try {
            const data = await API.get('/api/connection-status');
            this.updateStatus(data);
            return data;
        } catch (error) {
            console.error('Failed to fetch connection status:', error);
            this.updateStatus({ status: 'error', error: error.message });
            return null;
        }
    }
};

// ============================================
// Modal Management
// ============================================
const Modal = {
    /**
     * Show a modal by ID
     * @param {string} modalId - The modal element ID
     */
    show(modalId) {
        const modal = document.getElementById(modalId);
        const backdrop = document.getElementById(`${modalId}-backdrop`) || document.getElementById('modal-backdrop');

        if (modal) {
            modal.classList.add('active');
        }
        if (backdrop) {
            backdrop.classList.add('active');
        }
        document.body.style.overflow = 'hidden';
    },

    /**
     * Hide a modal by ID
     * @param {string} modalId - The modal element ID
     */
    hide(modalId) {
        const modal = document.getElementById(modalId);
        const backdrop = document.getElementById(`${modalId}-backdrop`) || document.getElementById('modal-backdrop');

        if (modal) {
            modal.classList.remove('active');
        }
        if (backdrop) {
            backdrop.classList.remove('active');
        }
        document.body.style.overflow = '';
    },

    /**
     * Create and show an alert modal
     * @param {object} options - Modal options
     */
    alert({ title, message, type = 'info', onClose }) {
        const id = Utils.generateId();
        const iconMap = {
            success: '<svg class="w-6 h-6 text-green-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>',
            error: '<svg class="w-6 h-6 text-red-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="15" y1="9" x2="9" y2="15"></line><line x1="9" y1="9" x2="15" y2="15"></line></svg>',
            warning: '<svg class="w-6 h-6 text-amber-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>',
            info: '<svg class="w-6 h-6 text-blue-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>'
        };

        const html = `
            <div id="${id}-backdrop" class="modal-backdrop"></div>
            <div id="${id}" class="modal">
                <div class="modal-header">
                    <div class="flex items-center gap-3">
                        ${iconMap[type] || iconMap.info}
                        <h3 class="modal-title">${Utils.escapeHtml(title)}</h3>
                    </div>
                    <button onclick="Modal.hide('${id}'); document.getElementById('${id}').remove(); document.getElementById('${id}-backdrop').remove();" class="btn btn-ghost btn-icon">
                        <svg class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                    </button>
                </div>
                <div class="modal-body">
                    <p class="text-zinc-300">${Utils.escapeHtml(message)}</p>
                </div>
                <div class="modal-footer">
                    <button onclick="Modal.hide('${id}'); document.getElementById('${id}').remove(); document.getElementById('${id}-backdrop').remove(); ${onClose ? 'onClose()' : ''}" class="btn btn-primary">
                        OK
                    </button>
                </div>
            </div>
        `;

        const container = document.getElementById('modals-container') || document.body;
        container.insertAdjacentHTML('beforeend', html);
        this.show(id);
    }
};

// ============================================
// Toast Notifications
// ============================================
const Toast = {
    container: null,

    /**
     * Initialize the toast container
     */
    init() {
        if (!this.container) {
            this.container = document.createElement('div');
            this.container.id = 'toast-container';
            this.container.className = 'fixed bottom-4 right-4 z-[9999] flex flex-col gap-2';
            document.body.appendChild(this.container);
        }
    },

    /**
     * Show a toast notification
     * @param {object} options - Toast options
     */
    show({ message, type = 'info', duration = 3000 }) {
        this.init();

        const id = Utils.generateId();
        const bgColors = {
            success: 'bg-green-500/10 border-green-500/20 text-green-400',
            error: 'bg-red-500/10 border-red-500/20 text-red-400',
            warning: 'bg-amber-500/10 border-amber-500/20 text-amber-400',
            info: 'bg-blue-500/10 border-blue-500/20 text-blue-400'
        };

        const toast = document.createElement('div');
        toast.id = id;
        toast.className = `px-4 py-3 rounded-lg border ${bgColors[type] || bgColors.info} text-sm font-medium shadow-lg transform translate-x-full opacity-0 transition-all duration-300`;
        toast.textContent = message;

        this.container.appendChild(toast);

        // Animate in
        requestAnimationFrame(() => {
            toast.classList.remove('translate-x-full', 'opacity-0');
        });

        // Auto remove
        setTimeout(() => {
            toast.classList.add('translate-x-full', 'opacity-0');
            setTimeout(() => toast.remove(), 300);
        }, duration);
    }
};

// ============================================
// Global Refresh Function
// ============================================
let isRefreshing = false;

async function refreshData() {
    if (isRefreshing) return;

    const btn = document.getElementById('refresh-btn');
    if (btn) {
        isRefreshing = true;
        btn.classList.add('animate-spin');
    }

    try {
        await ConnectionManager.fetch();

        // Dispatch custom event for page-specific refresh handlers
        window.dispatchEvent(new CustomEvent('app:refresh'));

        Toast.show({ message: 'Data refreshed', type: 'success', duration: 2000 });
    } catch (error) {
        Toast.show({ message: 'Refresh failed', type: 'error' });
    } finally {
        if (btn) {
            isRefreshing = false;
            btn.classList.remove('animate-spin');
        }
    }
}

// ============================================
// Initialize on DOM Ready
// ============================================
document.addEventListener('DOMContentLoaded', () => {
    // Inject mobile menu button
    const mobileMenuTemplate = document.getElementById('mobile-menu-btn-template');
    const mobileMenuContainer = document.getElementById('mobile-menu-container');
    if (mobileMenuTemplate && mobileMenuContainer) {
        mobileMenuContainer.appendChild(mobileMenuTemplate.content.cloneNode(true));
    }

    // Initial connection status fetch
    ConnectionManager.fetch();

    // Set up polling for connection status (every 10 seconds)
    setInterval(() => ConnectionManager.fetch(), 10000);
});
