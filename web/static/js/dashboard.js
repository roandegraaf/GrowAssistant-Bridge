/**
 * GrowAssistant Bridge - status page.
 * Verdict first, the grower's words in the rows; entity ids only inside Details groups.
 */

const INTEGRATION_LABELS = {
    esphome: 'ESPHome',
    mqtt: 'MQTT',
    gpio: 'GPIO',
    http: 'HTTP',
    serial: 'Serial',
    camera: 'Camera',
    simulator: 'Simulator',
};

const Dashboard = {
    actuators: [],
    connectionStatus: null,
    integrations: null,
    deviceData: null,
    telemetry: null,
    deviceDataRequestActive: false,

    init() {
        this.loadConnectionStatus().then(status => {
            if (status && status.ready === false && status.status !== 'error') return;
            this.loadAll();
        });

        setInterval(() => this.loadConnectionStatus(), 10000);
        setInterval(() => {
            if (!this.deviceDataRequestActive) this.loadDeviceData();
        }, 5000);
        setInterval(() => this.loadTelemetry(), 5000);

        this.bindEvents();
    },

    loadAll() {
        this.loadQueueInfo();
        this.loadIntegrations();
        this.loadDeviceTypes();
        this.loadActuators();
        this.loadDeviceData();
        this.loadTelemetry();
    },

    bindEvents() {
        const controlForm = document.getElementById('control-form');
        if (controlForm) controlForm.addEventListener('submit', (e) => this.sendCommand(e));

        const targetSelect = document.getElementById('control-target');
        if (targetSelect) targetSelect.addEventListener('change', () => this.updateActionOptions());

        const restartBtn = document.getElementById('restart-btn');
        if (restartBtn) restartBtn.addEventListener('click', () => this.restart());
    },

    humanize(id) {
        const raw = String(id || '');
        const local = raw.includes('.') ? raw.slice(raw.indexOf('.') + 1) : raw;
        const words = local.replace(/[_-]+/g, ' ').trim();
        return words ? words.charAt(0).toUpperCase() + words.slice(1) : raw;
    },

    integrationLabel(name) {
        const key = String(name || '').toLowerCase();
        return INTEGRATION_LABELS[key] || this.humanize(key);
    },

    formatValue(info) {
        const value = info.value;
        if (value === undefined || value === null) return '–';
        if (typeof value === 'boolean') return value ? 'On' : 'Off';
        if (typeof value === 'number') {
            const rounded = Number.isInteger(value) ? String(value) : value.toFixed(1);
            return info.unit ? `${rounded} ${info.unit}` : rounded;
        }
        if (typeof value === 'object') return 'Reporting';
        const text = String(value);
        if (/^(on|off)$/i.test(text)) return text.charAt(0).toUpperCase() + text.slice(1).toLowerCase();
        return info.unit ? `${text} ${info.unit}` : text;
    },

    setText(id, text) {
        const el = document.getElementById(id);
        if (el) el.textContent = text;
    },

    async loadConnectionStatus() {
        try {
            const data = await API.get('/api/connection-status');
            this.connectionStatus = data;
            if (!data.ready) {
                window.location.href = '/onboarding';
                return data;
            }
        } catch (error) {
            console.error('Error fetching connection status:', error);
            this.connectionStatus = { status: 'error', error: error.message };
        }
        this.renderConnection();
        this.renderVerdict();
        return this.connectionStatus;
    },

    renderConnection() {
        const status = this.connectionStatus;
        if (!status) return;
        const dot = document.getElementById('cloud-dot');
        const pill = document.getElementById('bridge-pill');

        let text = 'Not connected';
        let tone = 'critical';
        let pillText = 'Offline';
        if (status.status === 'connected') {
            text = 'Connected';
            tone = 'ok';
            pillText = 'Online';
        } else if (status.status === 'connecting') {
            text = 'Reconnecting';
            tone = 'attention';
            pillText = 'Reconnecting';
        }

        this.setText('cloud-state', text);
        if (dot) dot.className = `dot dot-${tone}`;
        if (pill) {
            pill.className = `pill pill-${tone}`;
            pill.textContent = pillText;
        }
    },

    renderVerdict() {
        const section = document.getElementById('bridge-verdict');
        if (!section) return;
        const status = this.connectionStatus || {};

        const devices = this.deviceData ? Object.values(this.deviceData) : [];
        const failing = devices.filter(d => d && d.error).length;
        const integrationCount = Array.isArray(this.integrations) ? this.integrations.length : null;

        const facts = [];
        if (status.status === 'connected') facts.push('Linked to your workspace');
        if (this.deviceData) facts.push(`${devices.length} ${devices.length === 1 ? 'device' : 'devices'}`);
        if (integrationCount !== null) {
            facts.push(`${integrationCount} ${integrationCount === 1 ? 'integration' : 'integrations'}`);
        }

        let tone = 'ok';
        let title = 'Everything is running';
        if (status.status === 'error') {
            tone = 'critical';
            title = "Can't reach the bridge service";
            facts.splice(0, facts.length, 'Try again in a moment, or restart the bridge');
        } else if (status.status === 'connecting' || !status.status) {
            tone = 'attention';
            title = 'Reconnecting to GrowAssistant';
            facts.unshift('Readings wait here until the connection is back');
        } else if (failing) {
            tone = 'attention';
            title = failing === 1 ? 'One device is not responding' : `${failing} devices are not responding`;
        }

        section.className = `verdict verdict-${tone}`;
        const dot = section.querySelector('.dot');
        if (dot) dot.className = `dot dot-${tone}`;
        this.setText('verdict-title', title);
        this.setText('verdict-detail', facts.join(' · '));
    },

    async loadQueueInfo() {
        try {
            const data = await API.get('/api/queue');
            this.setText('queue-size', data.size === 0 ? 'Nothing' : `${data.size} ${data.size === 1 ? 'reading' : 'readings'}`);
        } catch (error) {
            console.error('Error fetching queue info:', error);
            this.setText('queue-size', '–');
        }
    },

    async loadIntegrations() {
        const container = document.getElementById('integrations-container');
        try {
            const data = await API.get('/api/integrations');
            if (!Array.isArray(data) || data.length === 0) {
                container.innerHTML = `
                    <div class="empty-state">
                        <p class="empty-state-title">${Array.isArray(data) ? 'No integrations set up' : 'Integrations are starting'}</p>
                        <p class="m-0">${Array.isArray(data) ? 'Add one under Settings.' : 'Checking again shortly…'}</p>
                    </div>
                `;
                this.integrations = Array.isArray(data) ? [] : null;
                this.setText('integrations-count', '');
                if (!Array.isArray(data)) setTimeout(() => this.loadIntegrations(), 3000);
                this.renderVerdict();
                return;
            }

            this.integrations = data;
            this.setText('integrations-count', `${data.length} running`);
            container.innerHTML = data.map(integration => `
                <div class="li">
                    <span class="li-label">${Utils.escapeHtml(this.integrationLabel(integration.name))}</span>
                    <span class="li-v"><span class="pill pill-ok">Running</span></span>
                </div>
            `).join('');
            this.renderVerdict();
        } catch (error) {
            console.error('Error fetching integrations:', error);
            container.innerHTML = '<div class="card-body"><div class="alert alert-error">Couldn\'t load integrations. Retrying…</div></div>';
            this.setText('integrations-count', '');
            setTimeout(() => this.loadIntegrations(), 5000);
        }
    },

    async loadDeviceTypes() {
        const container = document.getElementById('devices-container');
        try {
            const data = await API.get('/api/device-types');
            const entries = Object.entries(data || {});
            if (entries.length === 0) {
                container.innerHTML = '<p class="m-0">No device types registered.</p>';
                return;
            }
            container.innerHTML = `<div class="log">${entries.map(([deviceType, actions]) => `
                <div><span class="log-time">${Utils.escapeHtml(deviceType)}</span>  ${
                    Array.isArray(actions) && actions.length ? actions.map(a => Utils.escapeHtml(a)).join(', ') : 'read only'
                }</div>
            `).join('')}</div>`;
        } catch (error) {
            console.error('Error fetching device types:', error);
            container.innerHTML = '<div class="alert alert-error">Couldn\'t load device types</div>';
        }
    },

    async loadActuators() {
        try {
            const data = await API.get('/api/actuators');
            if (!Array.isArray(data)) return;
            this.actuators = data;

            const targetSelect = document.getElementById('control-target');
            if (!targetSelect) return;

            targetSelect.innerHTML = '<option value="" selected disabled>Choose a device</option>';
            data.forEach(actuator => {
                const option = document.createElement('option');
                option.value = actuator.entityId;
                option.textContent = this.humanize(actuator.name || actuator.entityId);
                targetSelect.appendChild(option);
            });
        } catch (error) {
            console.error('Error fetching actuators:', error);
        }
    },

    updateActionOptions() {
        const targetSelect = document.getElementById('control-target');
        const actionSelect = document.getElementById('control-action');
        const payloadContainer = document.getElementById('payload-container');

        actionSelect.innerHTML = '<option value="" selected disabled>Choose an action</option>';
        payloadContainer.classList.add('hidden');

        if (!targetSelect.value) return;
        const actuator = this.actuators.find(a => a.entityId === targetSelect.value);
        const actions = actuator && Array.isArray(actuator.actions) ? actuator.actions : [];
        actions.forEach(action => {
            const option = document.createElement('option');
            option.value = action;
            option.textContent = this.humanize(action);
            actionSelect.appendChild(option);
        });
        if (actions.length > 0) payloadContainer.classList.remove('hidden');
    },

    showCommandResult(tone, message) {
        document.getElementById('command-result').innerHTML =
            `<div class="alert alert-${tone}">${message}</div>`;
        Modal.show('commandModal');
    },

    async sendCommand(event) {
        event.preventDefault();

        const target = document.getElementById('control-target').value;
        const action = document.getElementById('control-action').value;
        const actuator = this.actuators.find(a => a.entityId === target);
        const deviceName = this.humanize(actuator ? actuator.name : target);
        let payload = {};

        try {
            const payloadText = document.getElementById('control-payload').value;
            if (payloadText.trim()) payload = JSON.parse(payloadText);
        } catch (error) {
            this.showCommandResult('error', 'The command payload isn\'t valid JSON.');
            return;
        }

        try {
            const data = await API.post('/api/send-command', { target, action, payload });
            if (data.success) {
                this.showCommandResult('success',
                    `Sent <strong>${Utils.escapeHtml(this.humanize(action))}</strong> to <strong>${Utils.escapeHtml(deviceName)}</strong>.`);
            } else {
                this.showCommandResult('error', `Couldn't send the command: ${Utils.escapeHtml(data.error)}`);
            }
        } catch (error) {
            this.showCommandResult('error', `Couldn't send the command: ${Utils.escapeHtml(error.message)}`);
        }
    },

    async restart() {
        if (!confirm('Restart the bridge? Devices stay as they are; readings pause for a moment.')) return;
        try {
            const data = await API.post('/api/restart', {});
            Toast.show({ message: data.message || 'Restarting the bridge…' });
        } catch (error) {
            Toast.show({ message: `Couldn't restart: ${error.message}` });
        }
    },

    async loadTelemetry() {
        const container = document.getElementById('telemetry-container');
        if (!container) return;
        try {
            const data = await API.get('/api/telemetry');
            if (data.error) {
                container.innerHTML = `<div class="alert alert-error">${Utils.escapeHtml(data.error)}</div>`;
                return;
            }
            this.telemetry = data;

            const stats = data.stats || {};
            const dropped = (stats.dropped_no_entity || 0) + (stats.dropped_no_value || 0);
            this.setText('telemetry-stats', `${stats.published || 0} sent${dropped ? ` · ${dropped} skipped` : ''}`);
            this.setText('last-sync', stats.last_publish_ts
                ? new Date(stats.last_publish_ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
                : 'Not yet');
            if (typeof data.queueSize === 'number') {
                this.setText('queue-size', data.queueSize === 0 ? 'Nothing' : `${data.queueSize} ${data.queueSize === 1 ? 'reading' : 'readings'}`);
            }

            const entries = Object.entries(data.entities || {})
                .sort(([, a], [, b]) => new Date(b.ts || 0) - new Date(a.ts || 0));

            if (entries.length === 0) {
                container.innerHTML = `<p class="m-0">${data.connected ? 'Nothing sent yet. Waiting for the next cycle…' : 'Not connected to GrowAssistant yet.'}</p>`;
                return;
            }

            container.innerHTML = `<div class="log">${entries.map(([entityId, sample]) => {
                const time = sample.ts
                    ? new Date(sample.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
                    : '--:--:--';
                return `<div><span class="log-time">${Utils.escapeHtml(time)}</span>  ${Utils.escapeHtml(entityId)} = ${Utils.escapeHtml(String(sample.value))}</div>`;
            }).join('')}</div>`;
        } catch (error) {
            console.error('Error fetching telemetry:', error);
            container.innerHTML = '<div class="alert alert-error">Couldn\'t load the log</div>';
        }
    },

    async loadDeviceData() {
        this.deviceDataRequestActive = true;
        const container = document.getElementById('device-data-container');

        try {
            const data = await API.get('/api/devices');
            if (data.error) {
                container.innerHTML = `<div class="card-body"><div class="alert alert-error">${Utils.escapeHtml(data.error)}</div></div>`;
                return;
            }

            this.deviceData = data;
            const entries = Object.entries(data).sort(([a], [b]) => this.humanize(a).localeCompare(this.humanize(b)));
            this.setText('devices-count', entries.length ? `${entries.length} ${entries.length === 1 ? 'device' : 'devices'}` : '');
            this.renderVerdict();

            if (entries.length === 0) {
                container.innerHTML = `
                    <div class="empty-state">
                        <p class="empty-state-title">No devices yet</p>
                        <p class="m-0">Waiting for the first readings from your integrations…</p>
                    </div>
                `;
                return;
            }

            container.innerHTML = entries.map(([deviceId, info]) => {
                const name = Utils.escapeHtml(this.humanize(deviceId));
                if (info.error) {
                    return `<div class="li"><span class="li-label">${name}</span><span class="li-v"><span class="pill pill-critical">Not responding</span></span></div>`;
                }
                const seen = info.timestamp ? Utils.formatRelativeTime(info.timestamp) : '';
                return `
                    <div class="li">
                        <span class="li-label">${name}</span>
                        <span class="li-v">${seen ? `<span class="cap">${Utils.escapeHtml(seen)}</span>` : ''}<span class="li-strong">${Utils.escapeHtml(this.formatValue(info))}</span></span>
                    </div>
                `;
            }).join('');
        } catch (error) {
            console.error('Error fetching device data:', error);
            container.innerHTML = '<div class="card-body"><div class="alert alert-error">Couldn\'t load devices</div></div>';
        } finally {
            this.deviceDataRequestActive = false;
        }
    }
};
