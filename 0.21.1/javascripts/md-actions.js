// Copy-as-Markdown: fetch the page's raw .md (served alongside the HTML) and
// put it on the clipboard, so a reader can paste the page straight into an agent.
document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll(".md-page-actions__btn[data-md-src]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var label = btn.querySelector("span");
      fetch(btn.getAttribute("data-md-src"))
        .then(function (response) {
          if (!response.ok) {
            throw new Error("fetch failed");
          }
          return response.text();
        })
        .then(function (text) {
          return navigator.clipboard.writeText(text);
        })
        .then(function () {
          flash(btn, label, "Copied");
        })
        .catch(function () {
          flash(btn, label, "Copy failed");
        });
    });
  });

  function flash(btn, label, message) {
    if (!label) {
      return;
    }
    var original = label.textContent;
    label.textContent = message;
    btn.classList.add("md-page-actions__btn--flash");
    setTimeout(function () {
      label.textContent = original;
      btn.classList.remove("md-page-actions__btn--flash");
    }, 1500);
  }
});
