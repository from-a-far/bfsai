(() => {
  function normalize(value) {
    return (value || "").toLowerCase().replace(/\s+/g, " ").trim();
  }

  function loadClients() {
    const payload = document.getElementById("unmatched-clients-data");
    if (!(payload instanceof HTMLScriptElement) || !payload.textContent) {
      return [];
    }
    try {
      const clients = JSON.parse(payload.textContent);
      return Array.isArray(clients) ? clients : [];
    } catch {
      return [];
    }
  }

  function setupClientPicker(form, clients) {
    const searchInput = form.querySelector(".client-typeahead");
    const hiddenInput = form.querySelector(".client-po-box");
    const results = form.querySelector(".client-typeahead-results");
    const status = form.querySelector(".client-picker-status");
    if (
      !(searchInput instanceof HTMLInputElement) ||
      !(hiddenInput instanceof HTMLInputElement) ||
      !(results instanceof HTMLDivElement) ||
      !(status instanceof HTMLElement)
    ) {
      return;
    }

    let activeIndex = -1;
    let visibleMatches = [];

    function setStatus(message) {
      status.textContent = message;
    }

    function closeResults() {
      results.hidden = true;
      activeIndex = -1;
    }

    function selectedClient() {
      return clients.find((client) => client.po_box === hiddenInput.value) || null;
    }

    function renderResults(matches) {
      visibleMatches = matches.slice(0, 12);
      results.replaceChildren();
      activeIndex = -1;

      if (!visibleMatches.length) {
        const empty = document.createElement("div");
        empty.className = "client-typeahead-empty";
        empty.textContent = "No matching clients";
        results.appendChild(empty);
        results.hidden = false;
        return;
      }

      visibleMatches.forEach((client, index) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "client-typeahead-option";
        button.dataset.index = String(index);
        button.textContent = client.label;
        button.addEventListener("mousedown", (event) => {
          event.preventDefault();
        });
        button.addEventListener("click", () => {
          chooseClient(client);
        });
        results.appendChild(button);
      });
      results.hidden = false;
    }

    function syncActiveOption() {
      results.querySelectorAll(".client-typeahead-option").forEach((option, index) => {
        option.classList.toggle("active", index === activeIndex);
      });
    }

    function chooseClient(client) {
      searchInput.value = client.label;
      hiddenInput.value = client.po_box;
      setStatus(`Selected ${client.label}`);
      closeResults();
    }

    function findMatches(rawQuery) {
      const query = normalize(rawQuery);
      if (!query) {
        return clients.slice(0, 12);
      }
      return clients.filter((client) => normalize(`${client.label} ${client.search_text}`).includes(query));
    }

    function exactMatch(rawQuery) {
      const query = normalize(rawQuery);
      if (!query) {
        return null;
      }
      return (
        clients.find((client) => normalize(client.label) === query) ||
        clients.find((client) => normalize(client.po_box) === query) ||
        null
      );
    }

    function handleInput() {
      const exact = exactMatch(searchInput.value);
      if (exact) {
        hiddenInput.value = exact.po_box;
        setStatus(`Selected ${exact.label}`);
      } else {
        hiddenInput.value = "";
        setStatus("Type to search by last name, nickname, or PO box.");
      }
      renderResults(findMatches(searchInput.value));
      searchInput.setCustomValidity("");
    }

    searchInput.addEventListener("focus", () => {
      renderResults(findMatches(searchInput.value));
    });

    searchInput.addEventListener("input", () => {
      handleInput();
    });

    searchInput.addEventListener("keydown", (event) => {
      if (results.hidden || !visibleMatches.length) {
        return;
      }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        activeIndex = Math.min(activeIndex + 1, visibleMatches.length - 1);
        syncActiveOption();
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        activeIndex = Math.max(activeIndex - 1, 0);
        syncActiveOption();
        return;
      }
      if (event.key === "Enter" && activeIndex >= 0) {
        event.preventDefault();
        chooseClient(visibleMatches[activeIndex]);
        return;
      }
      if (event.key === "Escape") {
        closeResults();
      }
    });

    searchInput.addEventListener("blur", () => {
      window.setTimeout(() => {
        closeResults();
        const exact = exactMatch(searchInput.value);
        if (exact) {
          chooseClient(exact);
        }
      }, 120);
    });

    form.addEventListener("submit", (event) => {
      if (hiddenInput.value) {
        return;
      }
      const exact = exactMatch(searchInput.value);
      if (exact) {
        chooseClient(exact);
        return;
      }
      event.preventDefault();
      searchInput.setCustomValidity("Choose a client from the matching results.");
      searchInput.reportValidity();
      searchInput.focus();
      renderResults(findMatches(searchInput.value));
    });

    const initial = selectedClient();
    if (initial) {
      searchInput.value = initial.label;
      setStatus(`Selected ${initial.label}`);
    } else {
      setStatus("Type to search by last name, nickname, or PO box.");
    }
  }

  function setupPreviewModal() {
    const modal = document.getElementById("unmatched-preview-modal");
    const frame = document.getElementById("unmatched-preview-frame");
    const title = document.getElementById("unmatched-preview-title");
    const openLink = document.getElementById("unmatched-preview-open");
    if (
      !(modal instanceof HTMLElement) ||
      !(frame instanceof HTMLIFrameElement) ||
      !(title instanceof HTMLElement) ||
      !(openLink instanceof HTMLAnchorElement)
    ) {
      return;
    }

    function closeModal() {
      modal.hidden = true;
      frame.removeAttribute("src");
      document.body.classList.remove("modal-open");
    }

    function openModal(url, filename) {
      frame.src = url;
      title.textContent = filename;
      openLink.href = url;
      modal.hidden = false;
      document.body.classList.add("modal-open");
    }

    document.querySelectorAll(".unmatched-file-link").forEach((link) => {
      if (!(link instanceof HTMLAnchorElement)) {
        return;
      }
      link.addEventListener("click", (event) => {
        event.preventDefault();
        openModal(link.dataset.previewUrl || link.href, link.dataset.filename || link.textContent || "Unmatched document");
      });
    });

    modal.querySelectorAll("[data-close-unmatched-preview]").forEach((element) => {
      element.addEventListener("click", () => {
        closeModal();
      });
    });

    window.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !modal.hidden) {
        closeModal();
      }
    });
  }

  const clients = loadClients();
  document.querySelectorAll(".client-assign-form").forEach((form) => {
    if (form instanceof HTMLFormElement) {
      setupClientPicker(form, clients);
    }
  });
  setupPreviewModal();
})();
