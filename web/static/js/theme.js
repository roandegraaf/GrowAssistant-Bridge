(function () {
    var query = window.matchMedia('(prefers-color-scheme: dark)');
    function apply() {
        document.documentElement.classList.toggle('dark', query.matches);
    }
    apply();
    if (query.addEventListener) {
        query.addEventListener('change', apply);
    }
})();
