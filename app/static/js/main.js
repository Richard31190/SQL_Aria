document.addEventListener('DOMContentLoaded', () => {
    const themeToggle = document.getElementById('themeToggle');
    const html = document.documentElement;

    const savedTheme = localStorage.getItem('cq-theme') || 'dark';
    setTheme(savedTheme);

    themeToggle.addEventListener('click', () => {
        const currentTheme = html.getAttribute('data-bs-theme');
        const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
        setTheme(newTheme);
    });

    function setTheme(theme) {
        html.setAttribute('data-bs-theme', theme);
        localStorage.setItem('cq-theme', theme);

        if (theme === 'dark') {
            themeToggle.innerHTML = '<i class="bi bi-sun"></i> Mode Clair';
            themeToggle.className = 'btn btn-outline-light w-100 mb-2';
        } else {
            themeToggle.innerHTML = '<i class="bi bi-moon"></i> Mode Sombre';
            themeToggle.className = 'btn btn-outline-dark w-100 mb-2';
        }
    }
});