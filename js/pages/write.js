(function () {
  JDMS.initShell("community", "write");
  document.getElementById("footer").innerHTML = JDMS.renderFooter();

  var params = new URLSearchParams(location.search);
  var presetClass = params.get("class");

  var boardType = document.getElementById("board-type");
  var classGroup = document.getElementById("class-group");
  var classSelect = document.getElementById("class-id");

  classSelect.innerHTML = JDMS.CLASSES.map(function (id) {
    return '<option value="' + id + '">' + JDMS.formatClassLabel(id) + "</option>";
  }).join("");

  function syncBoardType() {
    classGroup.style.display = boardType.value === "class" ? "block" : "none";
  }

  if (presetClass && JDMS.CLASSES.indexOf(presetClass) !== -1) {
    boardType.value = "class";
    classSelect.value = presetClass;
  }
  syncBoardType();
  boardType.addEventListener("change", syncBoardType);

  document.getElementById("write-form").addEventListener("submit", function (e) {
    e.preventDefault();
    if (window.JDMSLoader) window.JDMSLoader.show();

    var post = {
      id: JDMS.generateId(),
      title: document.getElementById("title").value.trim(),
      content: document.getElementById("content").value.trim(),
      author: document.getElementById("author").value.trim(),
      category: document.getElementById("category").value,
      classId: boardType.value === "class" ? classSelect.value : null,
      createdAt: new Date().toISOString(),
      comments: [],
    };

    JDMS.addPost(post);
    location.href = "post.html?id=" + post.id;
  });
})();
