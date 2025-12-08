/**
 * GrowAssistant Bridge - Sidebar Module
 * Handles sidebar collapse/expand and mobile behavior
 */

const Sidebar = {
    element: null,
    overlay: null,
    toggleBtn: null,
    mainContent: null,
    isCollapsed: false,
    isMobileOpen: false,

    /**
     * Initialize the sidebar
     */
    init() {
        this.element = document.getElementById('sidebar');
        this.overlay = document.getElementById('sidebar-overlay');
        this.toggleBtn = document.getElementById('sidebar-toggle');
        this.mainContent = document.getElementById('main-content');
        this.mobileMenuBtn = document.getElementById('mobile-menu-btn');

        if (!this.element) return;

        // Restore collapsed state from localStorage
        this.isCollapsed = localStorage.getItem('sidebar_collapsed') === 'true';
        if (this.isCollapsed) {
            this.collapse(false);
        }

        // Bind events
        this.bindEvents();
    },

    /**
     * Bind event listeners
     */
    bindEvents() {
        // Desktop toggle button
        if (this.toggleBtn) {
            this.toggleBtn.addEventListener('click', () => this.toggle());
        }

        // Mobile menu button
        if (this.mobileMenuBtn) {
            this.mobileMenuBtn.addEventListener('click', () => this.toggleMobile());
        }

        // Mobile overlay click
        if (this.overlay) {
            this.overlay.addEventListener('click', () => this.closeMobile());
        }

        // Close mobile sidebar on escape key
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.isMobileOpen) {
                this.closeMobile();
            }
        });

        // Close mobile sidebar on navigation
        this.element.querySelectorAll('a').forEach(link => {
            link.addEventListener('click', () => {
                if (window.innerWidth < 1024) {
                    this.closeMobile();
                }
            });
        });

        // Handle resize events
        window.addEventListener('resize', Utils.debounce(() => {
            if (window.innerWidth >= 1024) {
                this.closeMobile();
            }
        }, 100));
    },

    /**
     * Toggle sidebar collapsed state (desktop)
     */
    toggle() {
        if (this.isCollapsed) {
            this.expand();
        } else {
            this.collapse();
        }
    },

    /**
     * Collapse the sidebar (desktop)
     * @param {boolean} animate - Whether to animate the transition
     */
    collapse(animate = true) {
        this.isCollapsed = true;
        localStorage.setItem('sidebar_collapsed', 'true');

        if (this.element) {
            this.element.classList.add('collapsed');
            // Hide text elements
            this.element.querySelectorAll('.sidebar-text').forEach(el => {
                el.style.opacity = '0';
                el.style.visibility = 'hidden';
            });
        }

        if (this.mainContent) {
            this.mainContent.style.marginLeft = 'var(--sidebar-collapsed)';
        }

        if (this.toggleBtn) {
            // Move button to collapsed position
            this.toggleBtn.style.left = 'calc(var(--sidebar-collapsed) - 12px)';
            const icon = this.toggleBtn.querySelector('#toggle-icon');
            if (icon) {
                icon.style.transform = 'rotate(180deg)';
            }
        }
    },

    /**
     * Expand the sidebar (desktop)
     */
    expand() {
        this.isCollapsed = false;
        localStorage.setItem('sidebar_collapsed', 'false');

        if (this.element) {
            this.element.classList.remove('collapsed');
            // Show text elements
            this.element.querySelectorAll('.sidebar-text').forEach(el => {
                el.style.opacity = '1';
                el.style.visibility = 'visible';
            });
        }

        if (this.mainContent) {
            this.mainContent.style.marginLeft = '';
        }

        if (this.toggleBtn) {
            // Move button to expanded position
            this.toggleBtn.style.left = 'calc(var(--sidebar-width) - 12px)';
            const icon = this.toggleBtn.querySelector('#toggle-icon');
            if (icon) {
                icon.style.transform = '';
            }
        }
    },

    /**
     * Toggle mobile sidebar
     */
    toggleMobile() {
        if (this.isMobileOpen) {
            this.closeMobile();
        } else {
            this.openMobile();
        }
    },

    /**
     * Open mobile sidebar
     */
    openMobile() {
        this.isMobileOpen = true;

        if (this.element) {
            this.element.classList.add('open');
        }

        if (this.overlay) {
            this.overlay.classList.add('active');
        }

        document.body.style.overflow = 'hidden';
    },

    /**
     * Close mobile sidebar
     */
    closeMobile() {
        this.isMobileOpen = false;

        if (this.element) {
            this.element.classList.remove('open');
        }

        if (this.overlay) {
            this.overlay.classList.remove('active');
        }

        document.body.style.overflow = '';
    }
};

// Initialize sidebar on DOM ready
document.addEventListener('DOMContentLoaded', () => {
    Sidebar.init();
});

// Re-bind mobile menu button after it's injected
document.addEventListener('DOMContentLoaded', () => {
    // Small delay to ensure the template is cloned
    setTimeout(() => {
        const mobileMenuBtn = document.getElementById('mobile-menu-btn');
        if (mobileMenuBtn) {
            mobileMenuBtn.addEventListener('click', () => Sidebar.toggleMobile());
        }
    }, 100);
});
