/**
 * GrowAssistant Bridge - Dashboard Module
 * Handles all dashboard-specific functionality
 */

const Dashboard = {
    // State
    deviceTypes: {},
    connectionStatus: null,
    deviceDataRequestActive: false,

    /**
     * Initialize the dashboard
     */
    init() {
        // Load initial data
        this.loadConnectionStatus().then(status => {
            // If not ready, redirect to onboarding
            if (!status || !status.ready) {
                window.location.href = '/onboarding';
                return;
            }

            this.loadQueueInfo();
            this.loadIntegrations();
            this.loadDeviceTypes();
            this.loadDeviceData();
        }).catch(error => {
            console.error('Error loading initial connection status:', error);
            // On error, try loading other data anyway
            this.loadQueueInfo();
            this.loadIntegrations();
            this.loadDeviceTypes();
            this.loadDeviceData();
        });

        // Set up polling
        setInterval(() => this.loadConnectionStatus(), 10000);
        setInterval(() => {
            if (!this.deviceDataRequestActive) {
                this.loadDeviceData();
            }
        }, 5000);

        // Bind event listeners
        this.bindEvents();

        // Listen for global refresh events
        window.addEventListener('app:refresh', () => {
            this.loadQueueInfo();
            this.loadIntegrations();
            this.loadDeviceTypes();
            this.loadDeviceData();
        });
    },

    /**
     * Bind event listeners
     */
    bindEvents() {
        const controlForm = document.getElementById('control-form');
        if (controlForm) {
            controlForm.addEventListener('submit', (e) => this.sendCommand(e));
        }

        const targetSelect = document.getElementById('control-target');
        if (targetSelect) {
            targetSelect.addEventListener('change', () => this.updateActionOptions());
        }
    },

    /**
     * Load connection status from API
     */
    async loadConnectionStatus() {
        try {
            const data = await API.get('/api/connection-status');

            this.connectionStatus = data;
            this.updateConnectionState();

            // Redirect to onboarding if not ready
            if (!data.ready) {
                window.location.href = '/onboarding';
                return data;
            }

            return data;
        } catch (error) {
            console.error('Error fetching connection status:', error);
            this.connectionStatus = { status: 'error', error: error.message };
            this.updateConnectionState();
            return this.connectionStatus;
        }
    },

    /**
     * Update UI based on connection state
     */
    updateConnectionState() {
        const stateElement = document.getElementById('connection-state');
        const stateIcon = document.getElementById('connection-state-icon');

        if (!this.connectionStatus || !stateElement) return;

        let stateText = '';
        let iconClass = '';

        switch (this.connectionStatus.status) {
            case 'ready':
                stateText = 'Ready';
                iconClass = 'success';
                break;
            case 'connected':
                stateText = 'Awaiting Space';
                iconClass = 'warning';
                break;
            case 'registration':
                stateText = 'Registration';
                iconClass = 'warning';
                break;
            case 'connection_timeout':
                stateText = 'Timed Out';
                iconClass = 'error';
                break;
            case 'not_registered':
            case 'not_connected':
                stateText = 'Disconnected';
                iconClass = 'error';
                break;
            case 'initializing':
                stateText = 'Initializing';
                iconClass = 'info';
                break;
            case 'busy':
                stateText = 'Busy';
                iconClass = 'info';
                break;
            case 'error':
                stateText = 'Error';
                iconClass = 'error';
                break;
            default:
                stateText = this.connectionStatus.status;
                iconClass = 'info';
        }

        stateElement.textContent = stateText;

        if (stateIcon) {
            stateIcon.className = `stat-icon ${iconClass}`;
        }

        this.checkApiStatus();
    },

    /**
     * Check and update API status
     */
    checkApiStatus() {
        const statusElement = document.getElementById('api-status');
        const statusIcon = document.getElementById('api-status-icon');

        if (!statusElement) return;

        if (this.connectionStatus) {
            if (this.connectionStatus.ready) {
                statusElement.textContent = 'Online';
                if (statusIcon) statusIcon.className = 'stat-icon success';
            } else if (this.connectionStatus.connected) {
                statusElement.textContent = 'Connecting';
                if (statusIcon) statusIcon.className = 'stat-icon warning';
            } else {
                statusElement.textContent = 'Offline';
                if (statusIcon) statusIcon.className = 'stat-icon error';
            }
        } else {
            statusElement.textContent = 'Unknown';
            if (statusIcon) statusIcon.className = 'stat-icon info';
        }
    },

    /**
     * Load queue information
     */
    async loadQueueInfo() {
        try {
            const data = await API.get('/api/queue');
            document.getElementById('queue-size').textContent = data.size;
        } catch (error) {
            console.error('Error fetching queue info:', error);
            document.getElementById('queue-size').textContent = '--';
        }
    },

    /**
     * Load integrations list
     */
    async loadIntegrations() {
        try {
            const data = await API.get('/api/integrations');
            const container = document.getElementById('integrations-container');
            const countElement = document.getElementById('integrations-count');

            if (!data || data.length === 0) {
                container.innerHTML = `
                    <div class="empty-state">
                        <svg class="empty-state-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                            <path d="M12 22v-5"></path>
                            <path d="M9 8V2"></path>
                            <path d="M15 8V2"></path>
                            <path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z"></path>
                        </svg>
                        <p class="empty-state-title">No integrations loaded</p>
                        <p class="text-sm">Checking again shortly...</p>
                    </div>
                `;
                countElement.textContent = '0';
                setTimeout(() => this.loadIntegrations(), 3000);
                return;
            }

            countElement.textContent = data.length;

            container.innerHTML = `
                <div class="space-y-2">
                    ${data.map(integration => `
                        <div class="flex items-center justify-between p-3 rounded-lg bg-surface-base border border-border-subtle">
                            <span class="font-medium text-zinc-200">${Utils.escapeHtml(integration.name)}</span>
                            <span class="badge badge-success">${Utils.escapeHtml(integration.type)}</span>
                        </div>
                    `).join('')}
                </div>
            `;
        } catch (error) {
            console.error('Error fetching integrations:', error);
            document.getElementById('integrations-container').innerHTML = `
                <div class="alert alert-error">
                    <span>Error loading integrations. Retrying...</span>
                </div>
            `;
            document.getElementById('integrations-count').textContent = '--';
            setTimeout(() => this.loadIntegrations(), 5000);
        }
    },

    /**
     * Load device types
     */
    async loadDeviceTypes() {
        try {
            const data = await API.get('/api/device-types');
            this.deviceTypes = data;

            const container = document.getElementById('devices-container');
            const targetSelect = document.getElementById('control-target');

            if (Object.keys(data).length === 0) {
                container.innerHTML = `
                    <div class="empty-state">
                        <svg class="empty-state-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                            <rect x="4" y="4" width="16" height="16" rx="2"></rect>
                            <rect x="9" y="9" width="6" height="6"></rect>
                        </svg>
                        <p class="empty-state-title">No device types registered</p>
                    </div>
                `;
                return;
            }

            // Update control target select
            targetSelect.innerHTML = '<option value="" selected disabled>Select a device</option>';

            // Build device types display
            let html = '<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">';

            for (const [deviceType, actions] of Object.entries(data)) {
                html += `
                    <div class="p-4 rounded-lg bg-surface-base border border-border-subtle">
                        <h4 class="font-medium text-zinc-200 mb-3">${Utils.escapeHtml(deviceType)}</h4>
                        <div class="flex flex-wrap gap-2">
                            ${Array.isArray(actions) && actions.length > 0
                                ? actions.map(action => `
                                    <span class="px-2 py-1 text-xs rounded bg-zinc-800 text-zinc-400">${Utils.escapeHtml(action)}</span>
                                `).join('')
                                : '<span class="text-xs text-zinc-500">No actions</span>'
                            }
                        </div>
                    </div>
                `;

                // Add to select
                const option = document.createElement('option');
                option.value = deviceType;
                option.textContent = deviceType;
                targetSelect.appendChild(option);
            }

            html += '</div>';
            container.innerHTML = html;
        } catch (error) {
            console.error('Error fetching device types:', error);
            document.getElementById('devices-container').innerHTML = `
                <div class="alert alert-error">Error loading device types</div>
            `;
        }
    },

    /**
     * Update action options based on selected device
     */
    updateActionOptions() {
        const targetSelect = document.getElementById('control-target');
        const actionSelect = document.getElementById('control-action');
        const payloadContainer = document.getElementById('payload-container');

        actionSelect.innerHTML = '<option value="" selected disabled>Select an action</option>';
        payloadContainer.classList.add('hidden');

        if (targetSelect.value) {
            const actions = this.deviceTypes[targetSelect.value] || [];

            if (Array.isArray(actions)) {
                actions.forEach(action => {
                    const option = document.createElement('option');
                    option.value = action;
                    option.textContent = action;
                    actionSelect.appendChild(option);
                });

                if (actions.length > 0) {
                    payloadContainer.classList.remove('hidden');
                }
            }
        }
    },

    /**
     * Send command to device
     */
    async sendCommand(event) {
        event.preventDefault();

        const target = document.getElementById('control-target').value;
        const action = document.getElementById('control-action').value;
        let payload = {};

        try {
            const payloadText = document.getElementById('control-payload').value;
            if (payloadText.trim()) {
                payload = JSON.parse(payloadText);
            }
        } catch (error) {
            document.getElementById('command-result').innerHTML = `
                <div class="alert alert-error">Invalid JSON payload</div>
            `;
            Modal.show('commandModal');
            return;
        }

        try {
            const data = await API.post('/api/send-command', { target, action, payload });

            if (data.success) {
                document.getElementById('command-result').innerHTML = `
                    <div class="alert alert-success">
                        Command sent successfully: <strong>${Utils.escapeHtml(action)}</strong> on <strong>${Utils.escapeHtml(target)}</strong>
                    </div>
                `;
            } else {
                document.getElementById('command-result').innerHTML = `
                    <div class="alert alert-error">Error: ${Utils.escapeHtml(data.error)}</div>
                `;
            }
        } catch (error) {
            document.getElementById('command-result').innerHTML = `
                <div class="alert alert-error">Error sending command: ${Utils.escapeHtml(error.message)}</div>
            `;
        }

        Modal.show('commandModal');
    },

    /**
     * Load device data
     */
    async loadDeviceData() {
        this.deviceDataRequestActive = true;

        try {
            const data = await API.get('/api/devices');
            const container = document.getElementById('device-data-container');

            if (data.error) {
                container.innerHTML = `<div class="alert alert-error">Error: ${Utils.escapeHtml(data.error)}</div>`;
                return;
            }

            if (Object.keys(data).length === 0) {
                container.innerHTML = `
                    <div class="empty-state">
                        <svg class="empty-state-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                            <path d="M22 12h-4l-3 9L9 3l-3 9H2"></path>
                        </svg>
                        <p class="empty-state-title">No device data available</p>
                        <p class="text-sm">Waiting for data from integrations...</p>
                    </div>
                `;
                return;
            }

            let html = '<div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">';

            for (const [deviceName, deviceInfo] of Object.entries(data)) {
                const hasError = deviceInfo.error;
                const value = deviceInfo.value !== undefined ? deviceInfo.value : 'N/A';
                const type = deviceInfo.type || 'unknown';
                const timestamp = deviceInfo.timestamp ? Utils.formatRelativeTime(deviceInfo.timestamp) : '';

                html += `
                    <div class="device-card ${hasError ? 'border-red-500/30' : ''}">
                        <div class="device-name">
                            <span class="w-2 h-2 rounded-full ${hasError ? 'bg-red-500' : 'bg-green-500'}"></span>
                            ${Utils.escapeHtml(deviceName)}
                        </div>
                        ${hasError
                            ? `<div class="text-red-400 text-sm">Error: ${Utils.escapeHtml(deviceInfo.error)}</div>`
                            : `
                                <div class="device-value">${Utils.escapeHtml(String(value))}</div>
                                <div class="flex items-center justify-between mt-2">
                                    <span class="text-xs text-zinc-500">${Utils.escapeHtml(type)}</span>
                                    ${timestamp ? `<span class="device-timestamp">${timestamp}</span>` : ''}
                                </div>
                            `
                        }
                    </div>
                `;
            }

            html += '</div>';
            container.innerHTML = html;
        } catch (error) {
            console.error('Error fetching device data:', error);
            document.getElementById('device-data-container').innerHTML = `
                <div class="alert alert-error">Error loading device data</div>
            `;
        } finally {
            this.deviceDataRequestActive = false;
        }
    }
};
