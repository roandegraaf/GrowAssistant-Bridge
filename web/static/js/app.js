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
        const tone = { success: 'success', error: 'error', warning: 'warning' }[type] || 'info';
        const html = `
            <div id="${id}-backdrop" class="modal-backdrop"></div>
            <div id="${id}" class="modal" role="dialog" aria-modal="true" aria-labelledby="${id}-title">
                <div class="modal-header">
                    <h3 class="modal-title" id="${id}-title">${Utils.escapeHtml(title)}</h3>
                </div>
                <div class="modal-body">
                    <div class="alert alert-${tone}">${Utils.escapeHtml(message)}</div>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-primary" data-close>OK</button>
                </div>
            </div>
        `;

        const container = document.getElementById('modals-container') || document.body;
        container.insertAdjacentHTML('beforeend', html);
        document.getElementById(id).querySelector('[data-close]').addEventListener('click', () => {
            this.hide(id);
            document.getElementById(id).remove();
            document.getElementById(`${id}-backdrop`).remove();
            if (onClose) onClose();
        });
        this.show(id);
    }
};

// ============================================
// Toast Notifications
// ============================================
const Toast = {
    container: null,

    init() {
        if (!this.container) {
            this.container = document.createElement('div');
            this.container.id = 'toast-container';
            this.container.setAttribute('role', 'status');
            this.container.className = 'fixed bottom-4 left-4 right-4 lg:left-auto z-50 flex flex-col items-end gap-2';
            document.body.appendChild(this.container);
        }
    },

    show({ message, duration = 3000 }) {
        this.init();

        const toast = document.createElement('div');
        toast.className = 'toast is-hidden';
        toast.textContent = message;
        this.container.appendChild(toast);

        requestAnimationFrame(() => toast.classList.remove('is-hidden'));
        setTimeout(() => {
            toast.classList.add('is-hidden');
            setTimeout(() => toast.remove(), 300);
        }, duration);
    }
};
