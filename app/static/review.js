(function () {
  const modal = document.getElementById("review-modal");
  if (!modal) {
    return;
  }

  const openButton = document.getElementById("open-review-modal");
  const closeButtons = modal.querySelectorAll("[data-close-review]");
  const pagesRoot = document.getElementById("review-pages");
  const instructions = document.getElementById("review-instructions");
  const alignmentsInput = document.getElementById("field-alignments-json");
  const fieldSpecs = JSON.parse(document.getElementById("review-field-specs").textContent);
  const pages = JSON.parse(document.getElementById("review-pages-data").textContent);
  const fieldAlignments = JSON.parse(document.getElementById("review-alignments-data").textContent || "{}");
  const fieldInputs = new Map();
  const pageElements = new Map();
  const pathParts = window.location.pathname.split("/");
  const documentId = pathParts[pathParts.length - 1];

  let activeField = null;
  let dragState = null;
  let isExtracting = false;

  function persistAlignments() {
    alignmentsInput.value = JSON.stringify(fieldAlignments);
  }

  function openModal() {
    modal.hidden = false;
    document.body.classList.add("modal-open");
  }

  function closeModal() {
    modal.hidden = true;
    document.body.classList.remove("modal-open");
  }

  function setActiveField(fieldName) {
    activeField = fieldName;
    modal.querySelectorAll("[data-field-label]").forEach((element) => {
      element.classList.toggle("active-field", element.dataset.fieldLabel === fieldName);
    });
    const match = fieldAlignments[fieldName];
    if (match && pageElements.has(match.page_number)) {
      pageElements.get(match.page_number).scrollIntoView({ block: "center", behavior: "smooth" });
      instructions.textContent = `Drag on page ${match.page_number} to replace the box for ${fieldName.replaceAll("_", " ")}.`;
    } else {
      instructions.textContent = `Draw a box on the document to extract ${fieldName.replaceAll("_", " ")}.`;
    }
    renderBoxes();
  }

  function renderBoxes() {
    pageElements.forEach((pageElement, pageNumber) => {
      const overlay = pageElement.querySelector(".review-page-overlay");
      overlay.querySelectorAll(".field-box").forEach((element) => element.remove());
      Object.entries(fieldAlignments).forEach(([fieldName, match]) => {
        if (!match || Number(match.page_number) !== Number(pageNumber) || !match.normalized_bbox) {
          return;
        }
        const box = document.createElement("div");
        box.className = "field-box";
        if (fieldName === activeField) {
          box.classList.add("active");
        }
        const bbox = match.normalized_bbox;
        box.style.left = `${bbox.left * 100}%`;
        box.style.top = `${bbox.top * 100}%`;
        box.style.width = `${bbox.width * 100}%`;
        box.style.height = `${bbox.height * 100}%`;
        box.title = `${fieldName}: ${match.match_text || match.value || ""}`;
        overlay.appendChild(box);
      });
    });
    persistAlignments();
  }

  function createSelectionBox(overlay) {
    const box = document.createElement("div");
    box.className = "field-box active draft";
    overlay.appendChild(box);
    return box;
  }

  function cancelSelection(message) {
    if (!dragState) {
      return;
    }
    dragState.boxElement.remove();
    dragState = null;
    if (message) {
      instructions.textContent = message;
    }
  }

  async function finalizeSelection(overlay, pageNumber) {
    if (!dragState || isExtracting) {
      return;
    }
    const { overlayRect, startX, startY, currentX, currentY, boxElement, pointerId } = dragState;
    const left = Math.max(0, Math.min(startX, currentX));
    const top = Math.max(0, Math.min(startY, currentY));
    const width = Math.abs(currentX - startX);
    const height = Math.abs(currentY - startY);
    boxElement.remove();
    dragState = null;
    if (overlay.hasPointerCapture(pointerId)) {
      overlay.releasePointerCapture(pointerId);
    }

    if (!activeField || width < 6 || height < 6) {
      instructions.textContent = "Selection ignored. Choose a field and drag a larger box.";
      return;
    }

    const bbox = {
      left: +(left / overlayRect.width).toFixed(4),
      top: +(top / overlayRect.height).toFixed(4),
      width: +(width / overlayRect.width).toFixed(4),
      height: +(height / overlayRect.height).toFixed(4),
    };
    instructions.textContent = `Extracting ${activeField.replaceAll("_", " ")} from page ${pageNumber}...`;
    isExtracting = true;
    try {
      const response = await fetch(`/api/documents/${documentId}/extract-box`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ field_name: activeField, page_number: pageNumber, bbox }),
      });
      if (!response.ok) {
        instructions.textContent = `Extraction failed for ${activeField.replaceAll("_", " ")}.`;
        return;
      }
      const payload = await response.json();
      fieldAlignments[activeField] = {
        value: payload.value,
        page_number: payload.page_number,
        match_text: payload.text,
        bbox: payload.bbox,
        normalized_bbox: payload.normalized_bbox,
        confidence: 1.0,
      };
      const input = fieldInputs.get(activeField);
      if (input) {
        input.value = payload.value ?? "";
        input.dispatchEvent(new Event("input", { bubbles: true }));
      }
      instructions.textContent = `Updated ${activeField.replaceAll("_", " ")} from page ${pageNumber}.`;
      renderBoxes();
    } catch (error) {
      instructions.textContent = `Extraction failed for ${activeField.replaceAll("_", " ")}.`;
    } finally {
      isExtracting = false;
    }
  }

  function bindPage(page) {
    const pageElement = document.createElement("section");
    pageElement.className = "review-page";
    pageElement.dataset.pageNumber = page.page_number;
    pageElement.innerHTML = `
      <div class="review-page-header">Page ${page.page_number}</div>
      <div class="review-page-canvas">
        <img src="${page.image_url}" alt="Page ${page.page_number}" draggable="false" />
        <div class="review-page-overlay"></div>
      </div>
    `;
    const overlay = pageElement.querySelector(".review-page-overlay");
    overlay.addEventListener("pointerdown", (event) => {
      if (!activeField || isExtracting) {
        instructions.textContent = "Select a field before drawing a box.";
        return;
      }
      if (dragState) {
        cancelSelection();
      }
      event.preventDefault();
      const rect = overlay.getBoundingClientRect();
      const boxElement = createSelectionBox(overlay);
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      dragState = {
        overlayRect: rect,
        startX: x,
        startY: y,
        currentX: x,
        currentY: y,
        boxElement,
        pointerId: event.pointerId,
      };
      boxElement.style.left = `${x}px`;
      boxElement.style.top = `${y}px`;
      boxElement.style.width = "0px";
      boxElement.style.height = "0px";
      overlay.setPointerCapture(event.pointerId);
    });

    overlay.addEventListener("pointermove", (event) => {
      if (!dragState) {
        return;
      }
      const x = Math.max(0, Math.min(dragState.overlayRect.width, event.clientX - dragState.overlayRect.left));
      const y = Math.max(0, Math.min(dragState.overlayRect.height, event.clientY - dragState.overlayRect.top));
      dragState.currentX = x;
      dragState.currentY = y;
      dragState.boxElement.style.left = `${Math.min(dragState.startX, x)}px`;
      dragState.boxElement.style.top = `${Math.min(dragState.startY, y)}px`;
      dragState.boxElement.style.width = `${Math.abs(x - dragState.startX)}px`;
      dragState.boxElement.style.height = `${Math.abs(y - dragState.startY)}px`;
    });

    overlay.addEventListener("pointerup", (event) => {
      if (!dragState || dragState.pointerId !== event.pointerId) {
        return;
      }
      const x = Math.max(0, Math.min(dragState.overlayRect.width, event.clientX - dragState.overlayRect.left));
      const y = Math.max(0, Math.min(dragState.overlayRect.height, event.clientY - dragState.overlayRect.top));
      dragState.currentX = x;
      dragState.currentY = y;
      finalizeSelection(overlay, page.page_number);
    });
    overlay.addEventListener("pointercancel", () => cancelSelection("Selection cancelled."));
    overlay.addEventListener("lostpointercapture", () => {
      if (dragState && !isExtracting) {
        cancelSelection();
      }
    });

    pagesRoot.appendChild(pageElement);
    pageElements.set(page.page_number, pageElement);
  }

  fieldSpecs.forEach((field) => {
    const input = modal.querySelector(`[data-field-name="${field.name}"]`);
    if (!input) {
      return;
    }
    fieldInputs.set(field.name, input);
    input.addEventListener("focus", () => setActiveField(field.name));
  });

  pages.forEach(bindPage);
  renderBoxes();

  openButton?.addEventListener("click", openModal);
  closeButtons.forEach((button) => button.addEventListener("click", closeModal));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !modal.hidden) {
      closeModal();
    }
  });
})();
