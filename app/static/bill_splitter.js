(() => {
  const uploadForm = document.querySelector("[data-upload-form]");
  const uploadInput = document.querySelector("[data-upload-input]");
  const uploadButton = document.querySelector("[data-upload-button]");
  const uploadLoading = document.querySelector("[data-upload-loading]");

  if (uploadForm && uploadInput && uploadButton && uploadLoading) {
    uploadForm.addEventListener("submit", (event) => {
      if (!uploadInput.files || uploadInput.files.length === 0) {
        event.preventDefault();
        return;
      }
      uploadButton.disabled = true;
      uploadButton.textContent = "Loading...";
      uploadLoading.hidden = false;
    });
  }

  const grid = document.querySelector("[data-batch-grid]");
  if (!grid) {
    return;
  }

  const cards = Array.from(grid.querySelectorAll("[data-page-card]"));
  const selectionForms = Array.from(document.querySelectorAll("[data-selection-form]"));
  const selectionInputs = Array.from(document.querySelectorAll("[data-selection-input]"));
  const selectionButtons = Array.from(document.querySelectorAll("[data-selection-button]"));
  const selectionCount = document.querySelector("[data-selection-count]");
  const saveForm = document.querySelector("[data-save-form]");
  const removeForm = document.querySelector("[data-remove-form]");
  const scrollKey = `bill-splitter-scroll:${window.location.pathname}`;
  const marquee = document.createElement("div");
  marquee.className = "page-selection-box";
  marquee.hidden = true;
  grid.appendChild(marquee);

  const selectedPages = new Set();
  let suppressClick = false;
  let pointerState = null;
  let selectionAnchorIndex = 0;
  let selectionFocusIndex = 0;

  function previewWidth() {
    return Math.min(window.innerWidth * 0.425, 525);
  }

  function updatePreviewDirection(card) {
    const gap = 16;
    const rect = card.getBoundingClientRect();
    const shouldFlipLeft = window.innerWidth - rect.right < previewWidth() + gap;
    card.classList.toggle("page-card-preview-left", shouldFlipLeft);
  }

  function restoreScrollPosition() {
    const savedScroll = window.sessionStorage.getItem(scrollKey);
    if (!savedScroll) {
      return;
    }
    window.sessionStorage.removeItem(scrollKey);
    const top = Number(savedScroll);
    if (Number.isNaN(top)) {
      return;
    }
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        window.scrollTo(0, top);
      });
    });
  }

  function persistScrollPosition() {
    window.sessionStorage.setItem(scrollKey, String(window.scrollY));
  }

  function findCard(target) {
    return target instanceof Element ? target.closest("[data-page-card]") : null;
  }

  function sortedSelectedPages() {
    return cards
      .map((card) => Number(card.dataset.pageNumber))
      .filter((pageNumber) => selectedPages.has(pageNumber));
  }

  function syncSelectionUi() {
    cards.forEach((card) => {
      const pageNumber = Number(card.dataset.pageNumber);
      card.classList.toggle("page-card-selected", selectedPages.has(pageNumber));
    });

    const values = sortedSelectedPages();
    const serialized = values.join(",");
    selectionInputs.forEach((input) => {
      input.value = serialized;
    });
    selectionButtons.forEach((button) => {
      button.disabled = values.length === 0;
    });
    if (selectionCount) {
      selectionCount.textContent = `${values.length} page${values.length === 1 ? "" : "s"} selected`;
    }
  }

  function setSelectionBoundsFromCard(card) {
    const index = cardIndex(card);
    if (index < 0) {
      return;
    }
    selectionAnchorIndex = index;
    selectionFocusIndex = index;
  }

  function setSelectionRange(startIndex, endIndex) {
    const lower = Math.min(startIndex, endIndex);
    const upper = Math.max(startIndex, endIndex);
    replaceSelection(cards.slice(lower, upper + 1).map((card) => Number(card.dataset.pageNumber)));
  }

  function applySelection(card, shouldSelect) {
    const pageNumber = Number(card.dataset.pageNumber);
    if (shouldSelect) {
      selectedPages.add(pageNumber);
    } else {
      selectedPages.delete(pageNumber);
    }
    selectionFocusIndex = Math.max(cardIndex(card), 0);
    if (shouldSelect || selectedPages.size === 0) {
      selectionAnchorIndex = selectionFocusIndex;
    }
    syncSelectionUi();
  }

  function selectOnly(card) {
    selectedPages.clear();
    selectedPages.add(Number(card.dataset.pageNumber));
    setSelectionBoundsFromCard(card);
    syncSelectionUi();
  }

  function replaceSelection(pageNumbers) {
    selectedPages.clear();
    pageNumbers.forEach((pageNumber) => {
      selectedPages.add(pageNumber);
    });
    syncSelectionUi();
  }

  function currentCard() {
    if (cards[selectionFocusIndex]) {
      return cards[selectionFocusIndex];
    }
    const activeCard = findCard(document.activeElement);
    if (activeCard) {
      return activeCard;
    }
    const selectedCard = cards.find((card) => selectedPages.has(Number(card.dataset.pageNumber)));
    return selectedCard || cards[0] || null;
  }

  function cardIndex(card) {
    return cards.indexOf(card);
  }

  function gridColumnCount() {
    if (cards.length < 2) {
      return 1;
    }
    const firstTop = cards[0].offsetTop;
    let count = 0;
    for (const card of cards) {
      if (card.offsetTop !== firstTop) {
        break;
      }
      count += 1;
    }
    return Math.max(count, 1);
  }

  function moveSelectionWithArrow(key) {
    const originCard = currentCard();
    if (!originCard) {
      return false;
    }
    const originIndex = selectionFocusIndex >= 0 ? selectionFocusIndex : cardIndex(originCard);
    if (originIndex < 0) {
      return false;
    }
    const columns = gridColumnCount();
    let targetIndex = originIndex;
    if (key === "ArrowRight") {
      targetIndex += 1;
    } else if (key === "ArrowLeft") {
      targetIndex -= 1;
    } else if (key === "ArrowDown") {
      targetIndex += columns;
    } else if (key === "ArrowUp") {
      targetIndex -= columns;
    } else {
      return false;
    }
    if (targetIndex < 0 || targetIndex >= cards.length) {
      return false;
    }
    const targetCard = cards[targetIndex];
    setSelectionRange(selectionAnchorIndex, targetIndex);
    selectionFocusIndex = targetIndex;
    targetCard.focus();
    return true;
  }

  function hideMarquee() {
    marquee.hidden = true;
    marquee.style.width = "0px";
    marquee.style.height = "0px";
  }

  function updateMarqueeBox(startX, startY, currentX, currentY) {
    const gridRect = grid.getBoundingClientRect();
    const left = Math.max(Math.min(startX, currentX) - gridRect.left, 0);
    const top = Math.max(Math.min(startY, currentY) - gridRect.top, 0);
    const right = Math.min(Math.max(startX, currentX) - gridRect.left, gridRect.width);
    const bottom = Math.min(Math.max(startY, currentY) - gridRect.top, gridRect.height);
    marquee.hidden = false;
    marquee.style.left = `${left}px`;
    marquee.style.top = `${top}px`;
    marquee.style.width = `${Math.max(right - left, 1)}px`;
    marquee.style.height = `${Math.max(bottom - top, 1)}px`;
    return {
      left: Math.min(startX, currentX),
      top: Math.min(startY, currentY),
      right: Math.max(startX, currentX),
      bottom: Math.max(startY, currentY),
    };
  }

  function intersectingPageNumbers(selectionRect) {
    return cards
      .filter((card) => {
        const rect = card.getBoundingClientRect();
        return !(
          rect.right < selectionRect.left ||
          rect.left > selectionRect.right ||
          rect.bottom < selectionRect.top ||
          rect.top > selectionRect.bottom
        );
      })
      .map((card) => Number(card.dataset.pageNumber));
  }

  grid.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) {
      return;
    }
    const card = findCard(event.target);
    event.preventDefault();
    suppressClick = false;
    pointerState = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      activeCard: card,
      dragged: false,
    };
    grid.setPointerCapture?.(event.pointerId);
  });

  grid.addEventListener("pointermove", (event) => {
    if (!pointerState || pointerState.pointerId !== event.pointerId) {
      return;
    }
    const deltaX = event.clientX - pointerState.startX;
    const deltaY = event.clientY - pointerState.startY;
    if (!pointerState.dragged && Math.hypot(deltaX, deltaY) < 8) {
      return;
    }
    pointerState.dragged = true;
    suppressClick = true;
    const selectionRect = updateMarqueeBox(pointerState.startX, pointerState.startY, event.clientX, event.clientY);
    replaceSelection(intersectingPageNumbers(selectionRect));
  });

  function finishPointerSelection(event) {
    if (!pointerState || pointerState.pointerId !== event.pointerId) {
      return;
    }
    const activeCard = pointerState.activeCard;
    const dragged = pointerState.dragged;
    pointerState = null;
    hideMarquee();
    if (!dragged && activeCard) {
      if (event.shiftKey) {
        applySelection(activeCard, !activeCard.classList.contains("page-card-selected"));
      } else {
        selectOnly(activeCard);
      }
    }
    window.setTimeout(() => {
      suppressClick = false;
    }, 0);
  }

  grid.addEventListener("pointerup", finishPointerSelection);

  grid.addEventListener("pointercancel", (event) => {
    if (!pointerState || pointerState.pointerId !== event.pointerId) {
      return;
    }
    pointerState = null;
    hideMarquee();
    suppressClick = false;
  });

  window.addEventListener("keydown", (event) => {
    const target = event.target;
    if (
      target instanceof HTMLElement &&
      (target.tagName === "BUTTON" || target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT")
    ) {
      return;
    }
    if (event.shiftKey && !event.metaKey && !event.ctrlKey && !event.altKey && moveSelectionWithArrow(event.key)) {
      event.preventDefault();
      return;
    }
    if (event.shiftKey || event.metaKey || event.ctrlKey || event.altKey) {
      return;
    }
    if (selectedPages.size === 0) {
      return;
    }
    if (event.key === "Enter" && saveForm) {
      event.preventDefault();
      saveForm.requestSubmit();
      return;
    }
    if ((event.key === "Delete" || event.key.toLowerCase() === "r") && removeForm) {
      event.preventDefault();
      removeForm.requestSubmit();
    }
  });

  cards.forEach((card) => {
    card.addEventListener("mouseenter", () => {
      updatePreviewDirection(card);
    });

    card.addEventListener("focus", () => {
      updatePreviewDirection(card);
    });

    card.addEventListener("click", (event) => {
      if (suppressClick) {
        event.preventDefault();
        return;
      }
      if (event.shiftKey) {
        applySelection(card, !card.classList.contains("page-card-selected"));
        return;
      }
      selectOnly(card);
    });

    card.addEventListener("keydown", (event) => {
      if (event.key !== " ") {
        return;
      }
      event.preventDefault();
      if (event.shiftKey) {
        const index = cardIndex(card);
        if (index >= 0) {
          setSelectionRange(selectionAnchorIndex, index);
          selectionFocusIndex = index;
          card.focus();
        }
      } else {
        selectOnly(card);
      }
    });
  });

  selectionForms.forEach((form) => {
    form.addEventListener("submit", () => {
      persistScrollPosition();
    });
  });

  window.addEventListener("resize", () => {
    cards.forEach((card) => {
      updatePreviewDirection(card);
    });
  });

  hideMarquee();
  if (cards.length > 0) {
    selectedPages.add(Number(cards[0].dataset.pageNumber));
    selectionAnchorIndex = 0;
    selectionFocusIndex = 0;
  }
  syncSelectionUi();
  restoreScrollPosition();
})();
