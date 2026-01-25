/**
 * GrowAssistant Bridge - Onboarding Module
 * Handles the device registration and connection flow
 */

const Onboarding = {
    // Configuration
    CODE_TIMEOUT_MS: 5 * 60 * 1000,  // 5 minutes
    POLL_INTERVAL_MS: 3000,           // 3 seconds
    REDIRECT_DELAY_MS: 3000,          // 3 seconds

    // State
    codeGeneratedTime: null,
    pollInterval: null,
    countdownInterval: null,
    redirectCountdown: null,
    currentStep: 1,
    currentStatus: null,

    /**
     * Initialize the onboarding flow
     * @param {object} options - Server-provided initial state
     */
    init(options = {}) {
        const { authCode, connectionTimedOut, isAuthenticated } = options;

        // Set initial code generation time if we have an auth code
        if (authCode && !connectionTimedOut) {
            this.codeGeneratedTime = Date.now();
        }

        // Bind event handlers
        this.bindEvents();

        // Load initial status and start polling
        this.loadConnectionStatus().then(() => {
            this.startPolling();
        });
    },

    /**
     * Bind event handlers
     */
    bindEvents() {
        // Copy code button
        const copyBtn = document.getElementById('copy-code-btn');
        if (copyBtn) {
            copyBtn.addEventListener('click', () => this.copyAuthCode());
        }

        // Get new code button
        const newCodeBtn = document.getElementById('get-new-code-btn');
        if (newCodeBtn) {
            newCodeBtn.addEventListener('click', () => this.requestNewCode());
        }
    },

    /**
     * Make a GET request to an API endpoint
     * @param {string} endpoint - The API endpoint
     * @returns {Promise<any>} - The JSON response
     */
    async apiGet(endpoint) {
        const response = await fetch(endpoint);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        return response.json();
    },

    /**
     * Make a POST request to an API endpoint
     * @param {string} endpoint - The API endpoint
     * @param {object} data - The data to send
     * @returns {Promise<any>} - The JSON response
     */
    async apiPost(endpoint, data = {}) {
        const response = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        return response.json();
    },

    /**
     * Load connection status from API
     */
    async loadConnectionStatus() {
        try {
            const data = await this.apiGet('/api/connection-status');

            // Track code generation time for new registration states
            if (data.status === 'registration' && !this.codeGeneratedTime) {
                this.codeGeneratedTime = Date.now();
            }

            // Check for client-side timeout
            if (data.status === 'registration' && this.codeGeneratedTime) {
                const elapsed = Date.now() - this.codeGeneratedTime;
                if (elapsed >= this.CODE_TIMEOUT_MS) {
                    data.status = 'connection_timeout';
                    this.codeGeneratedTime = null;
                }
            }

            // Clear timer when no longer in registration
            if (data.status !== 'registration' && data.status !== 'connection_timeout') {
                this.codeGeneratedTime = null;
            }

            this.currentStatus = data;
            this.updateUI(data);

            // If ready, redirect to dashboard
            if (data.status === 'ready' || data.ready) {
                this.startRedirectCountdown();
            }

            return data;
        } catch (error) {
            console.error('Error fetching connection status:', error);
            this.currentStatus = { status: 'error', error: error.message };
            return this.currentStatus;
        }
    },

    /**
     * Start polling for connection status
     */
    startPolling() {
        if (this.pollInterval) {
            clearInterval(this.pollInterval);
        }
        this.pollInterval = setInterval(() => this.loadConnectionStatus(), this.POLL_INTERVAL_MS);
    },

    /**
     * Stop polling
     */
    stopPolling() {
        if (this.pollInterval) {
            clearInterval(this.pollInterval);
            this.pollInterval = null;
        }
    },

    /**
     * Update UI based on connection status
     * @param {object} status - The connection status
     */
    updateUI(status) {
        // Determine step based on status
        let step = 1;
        switch (status.status) {
            case 'initializing':
            case 'not_registered':
                step = 1;
                break;
            case 'registration':
                step = 2;
                break;
            case 'connection_timeout':
                step = 2;
                break;
            case 'connected':
                step = 3;
                break;
            case 'ready':
                step = 4;
                break;
            default:
                step = 1;
        }

        this.updateStepper(step);
        this.showContent(status);
    },

    /**
     * Update the stepper UI
     * @param {number} currentStep - The current step (1-4)
     */
    updateStepper(currentStep) {
        this.currentStep = currentStep;

        for (let i = 1; i <= 4; i++) {
            const stepEl = document.getElementById(`step-${i}`);
            const connectorEl = document.getElementById(`connector-${i}`);

            if (stepEl) {
                stepEl.classList.remove('active', 'completed');
                if (i < currentStep) {
                    stepEl.classList.add('completed');
                } else if (i === currentStep) {
                    stepEl.classList.add('active');
                }
            }

            if (connectorEl) {
                connectorEl.classList.remove('completed');
                if (i < currentStep) {
                    connectorEl.classList.add('completed');
                }
            }
        }
    },

    /**
     * Show the appropriate content based on status
     * @param {object} status - The connection status
     */
    showContent(status) {
        // Hide all content sections
        const sections = [
            'content-initializing',
            'content-registration',
            'content-timeout',
            'content-connected',
            'content-ready'
        ];
        sections.forEach(id => {
            const el = document.getElementById(id);
            if (el) el.classList.add('hidden');
        });

        // Show the appropriate section
        let sectionId = 'content-initializing';

        switch (status.status) {
            case 'initializing':
            case 'not_registered':
            case 'busy':
                sectionId = 'content-initializing';
                break;
            case 'registration':
                sectionId = 'content-registration';
                this.updateAuthCodeDisplay(status.auth_code);
                this.startCountdown();
                break;
            case 'connection_timeout':
                sectionId = 'content-timeout';
                this.stopCountdown();
                break;
            case 'connected':
                sectionId = 'content-connected';
                this.stopCountdown();
                break;
            case 'ready':
                sectionId = 'content-ready';
                this.stopCountdown();
                break;
            default:
                sectionId = 'content-initializing';
        }

        const section = document.getElementById(sectionId);
        if (section) {
            section.classList.remove('hidden');
        }
    },

    /**
     * Update the auth code display
     * @param {string} code - The auth code to display
     */
    updateAuthCodeDisplay(code) {
        const display = document.getElementById('auth-code-display');
        if (display && code) {
            display.textContent = code;
        }
    },

    /**
     * Start the countdown timer
     */
    startCountdown() {
        if (this.countdownInterval) return; // Already running

        this.updateCountdownDisplay();
        this.countdownInterval = setInterval(() => this.updateCountdownDisplay(), 1000);
    },

    /**
     * Stop the countdown timer
     */
    stopCountdown() {
        if (this.countdownInterval) {
            clearInterval(this.countdownInterval);
            this.countdownInterval = null;
        }
    },

    /**
     * Update the countdown display
     */
    updateCountdownDisplay() {
        const countdownEl = document.getElementById('code-countdown');
        if (!countdownEl || !this.codeGeneratedTime) return;

        const elapsed = Date.now() - this.codeGeneratedTime;
        const remaining = Math.max(0, this.CODE_TIMEOUT_MS - elapsed);

        if (remaining <= 0) {
            countdownEl.innerHTML = '<span class="text-red-400">Code expired</span>';
            this.stopCountdown();
            // Trigger status refresh to show timeout state
            this.loadConnectionStatus();
            return;
        }

        const minutes = Math.floor(remaining / 60000);
        const seconds = Math.floor((remaining % 60000) / 1000);
        countdownEl.textContent = `Code expires in ${minutes}:${seconds.toString().padStart(2, '0')}`;
    },

    /**
     * Copy auth code to clipboard
     */
    async copyAuthCode() {
        const codeDisplay = document.getElementById('auth-code-display');
        const tooltip = document.getElementById('copy-tooltip');

        if (!codeDisplay) return;

        try {
            await navigator.clipboard.writeText(codeDisplay.textContent);
            if (tooltip) {
                tooltip.classList.add('show');
                setTimeout(() => tooltip.classList.remove('show'), 2000);
            }
        } catch (err) {
            console.error('Failed to copy:', err);
        }
    },

    /**
     * Request a new auth code
     */
    async requestNewCode() {
        const btn = document.getElementById('get-new-code-btn');
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = `
                <svg class="animate-spin" style="width: 18px; height: 18px;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M21 12a9 9 0 1 1-6.219-8.56"></path>
                </svg>
                Generating...
            `;
        }

        try {
            const response = await this.apiPost('/api/request-new-code', {});

            if (response.success && response.auth_code) {
                // Track when the code was generated
                this.codeGeneratedTime = Date.now();

                // Update status with new code
                this.currentStatus = {
                    status: 'registration',
                    auth_code: response.auth_code,
                    authenticated: false,
                    connected: false,
                    ready: false
                };

                this.updateUI(this.currentStatus);
            } else {
                alert('Failed to generate new code: ' + (response.error || 'Unknown error'));
            }
        } catch (error) {
            console.error('Error requesting new code:', error);
            alert('Failed to request new code: ' + error.message);
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = `
                    <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"></path>
                        <path d="M3 3v5h5"></path>
                        <path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"></path>
                        <path d="M16 16h5v5"></path>
                    </svg>
                    Get New Code
                `;
            }
        }
    },

    /**
     * Start the redirect countdown after successful connection
     */
    startRedirectCountdown() {
        this.stopPolling();
        this.stopCountdown();

        let secondsLeft = Math.floor(this.REDIRECT_DELAY_MS / 1000);
        const countdownEl = document.getElementById('redirect-countdown');

        const updateCountdown = () => {
            if (countdownEl) {
                countdownEl.textContent = `Redirecting to dashboard in ${secondsLeft}...`;
            }
            secondsLeft--;

            if (secondsLeft < 0) {
                clearInterval(this.redirectCountdown);
                window.location.href = '/';
            }
        };

        updateCountdown();
        this.redirectCountdown = setInterval(updateCountdown, 1000);
    }
};
