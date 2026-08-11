(function () {
  "use strict";
  var form = document.getElementById("skill-form");
  var source = document.getElementById("skill-markdown");
  var pane = document.getElementById("skill-editor");
  if (!form || !source || !pane || typeof Quill === "undefined") return;

  var editor = new Quill(pane, {
    theme: "snow",
    modules: {
      toolbar: [
        ["bold", "italic", "underline", "strike"],
        [{ header: [1, 2, 3, false] }],
        [{ list: "ordered" }, { list: "bullet" }],
        ["link", "code-block"],
        ["clean"]
      ]
    }
  });
  editor.root.innerHTML = typeof marked !== "undefined" ? marked.parse(source.value) : source.value;

  function toMarkdown() {
    var lines = [];
    var current = "";
    (editor.getContents().ops || []).forEach(function (op) {
      var text = typeof op.insert === "string" ? op.insert : "";
      var attributes = op.attributes || {};
      text.split("\n").forEach(function (part, index, values) {
        if (attributes.bold && part) part = "**" + part + "**";
        if (attributes.italic && part) part = "*" + part + "*";
        if (attributes.code && part) part = "`" + part + "`";
        if (attributes.link && part) part = "[" + part + "](" + attributes.link + ")";
        current += part;
        if (index < values.length - 1) {
          if (attributes.header) current = "#".repeat(attributes.header) + " " + current;
          if (attributes.list === "ordered") current = "1. " + current;
          if (attributes.list === "bullet") current = "- " + current;
          lines.push(current);
          current = "";
        }
      });
    });
    if (current) lines.push(current);
    return lines.join("\n").trimEnd();
  }

  document.querySelectorAll(".skill-tab").forEach(function (tab) {
    tab.addEventListener("click", function () {
      var markdown = tab.dataset.tab === "markdown";
      if (markdown) source.value = toMarkdown();
      else editor.root.innerHTML = typeof marked !== "undefined" ? marked.parse(source.value) : source.value;
      source.style.display = markdown ? "block" : "none";
      pane.style.display = markdown ? "none" : "block";
      document.querySelectorAll(".skill-tab").forEach(function (item) {
        item.classList.toggle("active", item === tab);
      });
    });
  });

  form.addEventListener("submit", function () {
    if (pane.style.display !== "none") source.value = toMarkdown();
  });
})();
