/* Hardware Comps Dashboard — client-side UX.
   Table switching, search/filter, pagination, add/edit modal,
   comp lookup modal, batch comps, sparklines, toasts. */

(function () {
  "use strict";

  var API_BASE = "";
  var currentTable = "personal_assets";
  var currentPage = 0;
  var pageSize = 25;
  var totalItems = 0;
  var allItems = [];        // full filtered list for pagination

  /* ── Init ── */
  document.addEventListener("DOMContentLoaded", function () {
    wireTableSwitcher();
    wireAddItemModal();
    wireRefreshComps();
    wireBatchComps();
    wireSearchFilter();
    wirePager();
    wireTableRowClicks();
    wireSearchPanel();
    // Initial load of the active table
    loadTable(currentTable, 0);
    // If RAM/SSD tab, load memory market sidebar
    if (currentTable === "ram_ssd_stockpile") {
      loadMemoryMarket();
    }
    // Wire search panel after DOM ready
    wireSearchPanel();
  });

  /* ── Auth: session cookie is sent automatically by the browser ── */
  function apiPath(path) {
    return API_BASE + path;
  }

  function fetchJSON(path, options) {
    var opts = Object.assign({
      headers: Object.assign({ "Content-Type": "application/json" }, (options && options.headers) || {}),
    }, options);
    return fetch(apiPath(path), opts).then(function (r) {
      if (r.status === 401) {
        window.location.href = "/login";
        throw new Error("unauthorized");
      }
      if (r.status === 403 || r.status === 404) {
        throw new Error("not found");
      }
      if (!r.ok) {
        return r.json().then(function (body) {
          throw new Error(body.detail || body.error || "API error " + r.status);
        }).catch(function () {
          throw new Error("API error " + r.status);
        });
      }
      return r.json();
    });
  }

  function fetchText(path, options) {
    var opts = options || {};
    return fetch(apiPath(path), opts).then(function (r) {
      if (!r.ok) throw new Error("fetch failed " + r.status);
      return r.text();
    });
  }

  /* ── Toast ── */
  function toast(msg, kind) {
    var existing = document.querySelector(".toast");
    if (existing) existing.remove();
    var el = document.createElement("div");
    el.className = "toast" + (kind === "error" ? " error" : kind === "success" ? " success" : "");
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(function () { el.remove(); }, 4000);
  }

  /* ── Table switcher ── */
  function wireTableSwitcher() {
    var btns = document.querySelectorAll("#tableSwitcher .switcher-btn");
    btns.forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        var table = btn.getAttribute("data-table");
        if (table === currentTable) return;
        // Update active state
        btns.forEach(function (b) { b.classList.remove("active"); });
        btn.classList.add("active");
        currentTable = table;
        currentPage = 0;
        allItems = [];
        // Toggle memory sidebar
        var sidebar = document.getElementById("memorySidebar");
        if (table === "ram_ssd_stockpile") {
          sidebar.hidden = false;
          loadMemoryMarket();
        } else {
          sidebar.hidden = true;
        }
        loadTable(table, 0);
      });
    });
  }

  /* ── Table headline stats ── */
  function renderStats(items) {
    var total = document.getElementById("statTotal");
    var soldAvg = document.getElementById("statSoldAvg");
    var activeAvg = document.getElementById("statActiveAvg");
    var portfolio = document.getElementById("statPortfolio");

    if (!items.length) {
      total.textContent = "0";
      soldAvg.textContent = "—";
      activeAvg.textContent = "—";
      portfolio.textContent = "—";
      return;
    }
    total.textContent = String(items.length);

    var soldVals = items.map(function (i) {
      if (i.ebay_sold_avg_cents) return i.ebay_sold_avg_cents;
      return null;
    }).filter(Boolean);
    var activeVals = items.map(function (i) {
      if (i.ebay_active_avg_cents) return i.ebay_active_avg_cents;
      return null;
    }).filter(Boolean);

    soldAvg.textContent = soldVals.length
      ? "$" + (soldVals.reduce(function (a, b) { return a + b; }, 0) / soldVals.length / 100).toFixed(2)
      : "—";
    activeAvg.textContent = activeVals.length
      ? "$" + (activeVals.reduce(function (a, b) { return a + b; }, 0) / activeVals.length / 100).toFixed(2)
      : "—";

    var portfolioVals = items.map(function (i) {
      var v = i.ebay_sold_avg_cents || i.ebay_active_avg_cents;
      return v || 0;
    });
    var pTotal = portfolioVals.reduce(function (a, b) { return a + b; }, 0);
    portfolio.textContent = pTotal > 0 ? "$" + (pTotal / 100).toFixed(2) : "—";
  }

  /* ── Load table (paginated) ── */
  function loadTable(table, page) {
    var body = document.getElementById("inventoryBody");
    body.innerHTML = '<tr><td colspan="10" class="empty-row shimmer">Loading…</td></tr>';

    var offset = page * pageSize;
    fetchJSON("/api/inventory?table_type=" + encodeURIComponent(table) + "&limit=" + pageSize + "&offset=" + offset)
      .then(function (data) {
        allItems = data.items || [];
        totalItems = data.total || 0;
        renderTableRows(allItems);
        renderStats(allItems);
        currentPage = page;
        updatePager();
      })
      .catch(function (err) {
        body.innerHTML = '<tr><td colspan="10" class="empty-row">Error loading inventory. ' + escapeHtml(err.message) + '</td></tr>';
      });
  }

  /* ── Render rows ── */
  function renderTableRows(items) {
    var body = document.getElementById("inventoryBody");
    if (!items.length) {
      body.innerHTML = '<tr><td colspan="10" class="empty-row">No items yet. Click "+ Add Item" to get started.</td></tr>';
      return;
    }

    var html = "";
    items.forEach(function (item) {
      html += renderRow(item);
    });
    body.innerHTML = html;
  }

  function renderRow(item) {
    var sold = item.ebay_sold_avg_cents ? ("$" + (item.ebay_sold_avg_cents / 100).toFixed(2)) : "—";
    var active = item.ebay_active_avg_cents ? ("$" + (item.ebay_active_avg_cents / 100).toFixed(2)) : "—";
    var buy = item.acquisition_cost_cents ? ("$" + (item.acquisition_cost_cents / 100).toFixed(2)) : "—";
    var lastLooked = item.ebay_last_looked_at ? formatDate(item.ebay_last_looked_at) : "—";

    var trendHtml = sparklineHtml(item.id);

    var checked = "";
    if (item.id) {
      checked = '<td class="checkbox-cell"><input type="checkbox" class="row-checkbox" data-id="' + item.id + '" /></td>';
    }

    var actions = "";
    if (item.id) {
      actions = '<div class="table-actions">' +
        '<button class="btn lookup-comp-btn" data-id="' + item.id + '" title="Run eBay comps">Comps</button>' +
        '<button class="btn edit-item-btn" data-id="' + item.id + '" title="Edit">Edit</button>' +
        (item.table_type === "ram_ssd_stockpile" ? '<button class="btn log-event-btn" data-id="' + item.id + '" title="Log price event">Log event</button>' : "") +
        "</div>";
    }

    return '<tr>' +
      '<td><strong>' + escapeHtml(item.name) + '</strong></td>' +
      '<td><span class="table-type-badge ' + cssClass(item.table_type) + '">' + humanLabel(item.table_type) + '</span></td>' +
      '<td>' + escapeHtml(item.make || "") + (item.make && item.model ? " " : "") + escapeHtml(item.model || "") + '</td>' +
      '<td>' + (item.condition ? escapeHtml(item.condition) : "—") + '</td>' +
      '<td class="cents">' + buy + '</td>' +
      '<td class="cents">' + sold + '</td>' +
      '<td class="cents">' + active + '</td>' +
      trendHtml +
      '<td class="last-looked">' + lastLooked + '</td>' +
      (checked + actions) +
      '</tr>';
  }

  function cssClass(type) {
    return (type || "").replace(/_/g, "-");
  }

  function humanLabel(type) {
    var map = {
      "personal_assets": "Personal",
      "goodwill_flips": "Goodwill",
      "liquidation_pallets": "Pallets",
      "ram_ssd_stockpile": "RAM/SSD",
      "device_farm": "Devices",
    };
    return map[type] || type;
  }

  /* ── Sparklines (inline SVG) ── */
  function sparklineHtml(itemId) {
    // We render sparklines client-side from fetched price history (loaded on row hover or via a small data attr).
    // For initial render, show a placeholder that gets filled lazily.
    var dataHint = "";
    if (itemId) {
      dataHint = ' data-sparkline-id="' + itemId + '"';
    }
    return '<td class="sparkline-cell"><svg class="sparkline flat" viewBox="0 0 80 28" preserveAspectRatio="none"' + dataHint + '><path d=""/></svg></td>';
  }

  function renderSparkline(svgEl, snapshots) {
    if (!snapshots || snapshots.length < 2) {
      svgEl.classList.remove("up", "down");
      svgEl.classList.add("flat");
      svgEl.querySelector("path").setAttribute("d", "");
      svgEl.title = "No trend data";
      return;
    }
    // snapshots come in DESC order from API; reverse to time-ascending for the sparkline
    var sorted = snapshots.slice().sort(function (a, b) {
      return new Date(a.snapshot_at || 0) - new Date(b.snapshot_at || 0);
    });
    var values = sorted.map(function (s) {
      var v = s.sold_avg_cents || s.active_avg_cents;
      return v ? v / 100 : null;
    }).filter(function (v) { return v !== null && isFinite(v); });
    if (values.length < 2) {
      svgEl.classList.remove("up", "down");
      svgEl.classList.add("flat");
      svgEl.querySelector("path").setAttribute("d", "");
      svgEl.title = "Insufficient trend data";
      return;
    }
    var w = 80, h = 28;
    var min = Math.min.apply(null, values);
    var max = Math.max.apply(null, values);
    var range = Math.max(max - min, 0.01);
    var points = values.map(function (v, i) {
      var x = (i / (values.length - 1)) * w;
      var y = h - ((v - min) / range) * (h - 4) - 2;
      return x.toFixed(1) + "," + y.toFixed(1);
    });
    var d = "M" + points.join(" L");
    svgEl.querySelector("path").setAttribute("d", d);
    // Color by direction
    var first = values[0], last = values[values.length - 1];
    var diff = last - first;
    if (diff > 0.01) {
      svgEl.classList.remove("flat", "down");
      svgEl.classList.add("up");
    } else if (diff < -0.01) {
      svgEl.classList.remove("flat", "up");
      svgEl.classList.add("down");
    } else {
      svgEl.classList.remove("up", "down");
      svgEl.classList.add("flat");
    }
    svgEl.title = "$" + first.toFixed(2) + " → $" + last.toFixed(2) + " (" + (diff >= 0 ? "+" : "") + (diff / first * 100).toFixed(1) + "%)";
  }

  /* ── Lazy sparkline load on row hover (avoids N API calls on render) ── */
  function wireTableRowClicks() {
    var tableBody = document.getElementById("inventoryBody");
    var debounceTimer = null;

    tableBody.addEventListener("mouseover", function (e) {
      var row = e.target.closest("tr");
      if (!row) return;
      var sparkCell = row.querySelector(".sparkline-cell");
      if (!sparkCell || sparkCell.dataset.loaded) return;
      var svg = sparkCell.querySelector("svg");
      if (!svg) return;
      var itemId = svg.getAttribute("data-sparkline-id");
      if (!itemId) return;

      // Debounce per cell
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(function () {
        if (svg.dataset.loaded) return;
        fetchJSON("/api/comps/history?item_id=" + itemId + "&limit=60")
          .then(function (data) {
            renderSparkline(svg, data.snapshots || []);
            svg.dataset.loaded = "1";
          })
          .catch(function () {
            svg.classList.add("flat");
            svg.querySelector("path").setAttribute("d", "");
            svg.title = "Trend unavailable";
            svg.dataset.loaded = "1";
          });
      }, 180);
    });
  }

  /* ── Add / edit item modal ── */
  function wireAddItemModal() {
    var modal = document.getElementById("itemModal");
    var btn = document.getElementById("addItemBtn");
    var cancel = document.getElementById("modalCancelBtn");
    var form = document.getElementById("itemForm");

    btn.addEventListener("click", function () {
      document.getElementById("modalTitle").textContent = "Add Item";
      form.reset();
      document.getElementById("itemId").value = "";
      document.getElementById("itemTableType").value = currentTable;
      // Pre-fill search_query from item name (convenience)
      document.getElementById("itemSearchQuery").value = "";
      modal.hidden = false;
    });

    cancel.addEventListener("click", function () {
      modal.hidden = true;
    });
    modal.addEventListener("click", function (e) {
      if (e.target === modal) modal.hidden = true;
    });

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var formData = new FormData(form);
      var rawId = formData.get("id");
      var payloadId = rawId && rawId !== "" ? parseInt(rawId, 10) : null;
      var payload = {
        id: payloadId,
        table_type: formData.get("table_type"),
        name: formData.get("name").trim(),
        category: formData.get("category").trim() || null,
        make: formData.get("make").trim() || null,
        model: formData.get("model").trim() || null,
        serial_number: formData.get("serial_number").trim() || null,
        condition: formData.get("condition").trim() || null,
        notes: formData.get("notes").trim() || null,
        acquisition_cost_cents: formData.get("acquisition_cost_cents") ? Math.round(parseFloat(formData.get("acquisition_cost_cents")) * 100) : null,
        buy_source: formData.get("buy_source").trim() || null,
        search_query: formData.get("search_query").trim() || null,
        metadata_json: null,
      };
      if (!payload.name) {
        toast("Name is required", "error");
        return;
      }
      var method = payload.id ? "POST" : "POST"; // upsert is POST for both create/update in our API
      fetchJSON("/api/inventory", {
        method: "POST",
        body: JSON.stringify(payload),
      })
        .then(function (data) {
          toast("Item saved", "success");
          modal.hidden = true;
          loadTable(currentTable, 0);
        })
        .catch(function (err) {
          toast("Save failed: " + err.message, "error");
        });
    });
  }

  /* ── Edit existing item ── */
  function wireEditItem(itemId) {
    var modal = document.getElementById("itemModal");
    var form = document.getElementById("itemForm");
    document.getElementById("modalTitle").textContent = "Edit Item";

    fetchJSON("/api/inventory/" + itemId)
      .then(function (data) {
        var item = data.item;
        document.getElementById("itemId").value = item.id || "";
        document.getElementById("itemTableType").value = item.table_type || "personal_assets";
        document.getElementById("itemName").value = item.name || "";
        document.getElementById("itemCategory").value = item.category || "";
        document.getElementById("itemMake").value = item.make || "";
        document.getElementById("itemModel").value = item.model || "";
        document.getElementById("itemSerial").value = item.serial_number || "";
        document.getElementById("itemCondition").value = item.condition || "";
        document.getElementById("itemBuyCost").value = item.acquisition_cost_cents ? (item.acquisition_cost_cents / 100).toFixed(2) : "";
        document.getElementById("itemBuySource").value = item.buy_source || "";
        document.getElementById("itemSearchQuery").value = item.search_query || "";
        document.getElementById("itemNotes").value = item.notes || "";
        modal.hidden = false;
      })
      .catch(function (err) {
        toast("Failed to load item: " + err.message, "error");
      });

    // Reuse the same save handler — it checks for id
  }

  /* ── Comp lookup modal ── */
  function wireRefreshComps() {
    document.getElementById("refreshCompsBtn").addEventListener("click", function () {
      // Find first checked item, or the first row if none checked
      var checked = document.querySelector(".row-checkbox:checked");
      var itemId = checked ? parseInt(checked.getAttribute("data-id"), 10) : null;
      if (!itemId) {
        var firstRow = document.querySelector("#inventoryBody tr");
        var firstComp = firstRow ? firstRow.querySelector(".lookup-comp-btn") : null;
        if (firstComp) {
          itemId = parseInt(firstComp.getAttribute("data-id"), 10);
        }
      }
      if (!itemId) {
        toast("No item selected. Select an item or check a row.", "error");
        return;
      }
      openCompModal(itemId);
    });
  }

  function openCompModal(itemId) {
    var modal = document.getElementById("compModal");
    document.getElementById("compItemId").value = itemId;
    document.getElementById("compQuery").value = "";
    document.getElementById("compLimit").value = 50;
    document.getElementById("compResult").hidden = true;
    modal.hidden = false;
  }

  function wireCompModal() {
    var modal = document.getElementById("compModal");
    var form = document.getElementById("compForm");
    var cancel = document.getElementById("compCancelBtn");

    cancel.addEventListener("click", function () {
      modal.hidden = true;
    });
    modal.addEventListener("click", function (e) {
      if (e.target === modal) modal.hidden = true;
    });

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var formData = new FormData(form);
      var itemId = parseInt(formData.get("item_id"), 10);
      var query = formData.get("query").trim();
      var limit = parseInt(formData.get("limit"), 10) || 50;

      var btn = document.getElementById("compRunBtn");
      btn.disabled = true;
      btn.textContent = "Running…";

      // First load the item to get its default search_query if the user left the override blank
      fetchJSON("/api/inventory/" + itemId)
        .then(function (data) {
          var item = data.item;
          var ebayQuery = query || item.search_query || item.name;
          if (!ebayQuery) throw new Error("Item has no search query");
          return fetchJSON("/api/comps/lookup", {
            method: "POST",
            body: JSON.stringify({
              item_id: itemId,
              query: ebayQuery,
              limit: limit,
              table_type: item.table_type || "",
            }),
          });
        })
        .then(function (result) {
          showCompResult(itemId, result);
          toast("Comps refreshed", "success");
        })
        .catch(function (err) {
          toast("Comp lookup failed: " + err.message, "error");
        })
        .finally(function () {
          btn.disabled = false;
          btn.textContent = "Run Comps";
        });
    });
  }

  function showCompResult(itemId, result) {
    var summary = document.getElementById("compSummary");
    var itemsBox = document.getElementById("compItems");
    var activeAvg = result.active_avg || 0;
    var soldAvg = result.sold_avg || 0;

    summary.innerHTML =
      '<div class="comp-stat"><div class="comp-stat-label">Sold Avg</div><div class="comp-stat-value">' + fmtMoney(soldAvg) + '</div></div>' +
      '<div class="comp-stat"><div class="comp-stat-label">Active Avg</div><div class="comp-stat-value">' + fmtMoney(activeAvg) + '</div></div>' +
      '<div class="comp-stat"><div class="comp-stat-label">Sold Listings</div><div class="comp-stat-value">' + (result.sold_count || 0) + '</div></div>' +
      '<div class="comp-stat"><div class="comp-stat-label">Active Listings</div><div class="comp-stat-value">' + (result.active_count || 0) + '</div></div>';

    var compItems = result.sold_items.concat(result.active_items).slice(0, 12);
    itemsBox.innerHTML = compItems.map(function (ci) {
      var img = ci.image_url ? '<img src="' + escapeHtml(ci.image_url) + '" alt="" loading="lazy" />' : "";
      return '<div class="comp-item">' +
        img +
        '<div class="comp-item-title">' + escapeHtml(ci.title || "Unknown") + '</div>' +
        '<div class="comp-item-price">' + fmtMoney(ci.price) + '</div>' +
        '<div class="comp-item-condition">' + escapeHtml(ci.condition || "") + '</div>' +
        "</div>";
    }).join("");

    document.getElementById("compResult").hidden = false;
  }

  /* ── Batch comps ── */
  function wireBatchComps() {
    var modal = document.getElementById("batchModal");
    var cancel = document.getElementById("batchCancelBtn");
    var runBtn = document.getElementById("batchRunBtn");

    cancel.addEventListener("click", function () {
      modal.hidden = true;
    });
    modal.addEventListener("click", function (e) {
      if (e.target === modal) modal.hidden = true;
    });

    runBtn.addEventListener("click", function () {
      var checked = document.querySelectorAll(".row-checkbox:checked");
      var ids = [];
      checked.forEach(function (cb) {
        ids.push(parseInt(cb.getAttribute("data-id"), 10));
      });
      if (!ids.length) {
        toast("Select items to batch-comp using the row checkboxes.", "error");
        return;
      }
      modal.hidden = true;

      runBtn.disabled = true;
      runBtn.textContent = "Running batch…" ;

      fetchJSON("/api/comps/batch", {
        method: "POST",
        body: JSON.stringify({ item_ids: ids }),
      })
        .then(function (data) {
          var results = data.results || [];
          var ok = results.filter(function (r) { return r.ok; }).length;
          var failed = results.length - ok;
          toast("Batch complete: " + ok + " ok, " + failed + " failed", failed ? "error" : "success");
          loadTable(currentTable, 0);
          // Refresh memory market feed (batch writes it)
          if (currentTable === "ram_ssd_stockpile") loadMemoryMarket();
        })
        .catch(function (err) {
          toast("Batch failed: " + err.message, "error");
        })
        .finally(function () {
          runBtn.disabled = false;
          runBtn.textContent = "Run Batch";
        });
    });
  }

  /* ── Search / filter ── */
  function wireSearchFilter() {
    var searchInput = document.getElementById("itemSearch");
    var conditionFilter = document.getElementById("conditionFilter");
    var clearBtn = document.getElementById("clearFiltersBtn");

    function applyFilters() {
      var q = (searchInput.value || "").toLowerCase().trim();
      var cond = conditionFilter.value;
      var filtered = allItems.filter(function (item) {
        if (q) {
          var haystack = (item.name || "") + " " + (item.make || "") + " " + (item.model || "") + " " + (item.serial_number || "") + " " + (item.category || "");
          if (haystack.toLowerCase().indexOf(q) === -1) return false;
        }
        if (cond && item.condition !== cond) return false;
        return true;
      });
      // Re-render with filtered list, keeping current page where possible
      renderTableRows(filtered);
      renderStats(filtered);
      // Recalculate pager based on filtered set
      var maxPage = Math.max(0, Math.ceil(filtered.length / pageSize) - 1);
      if (currentPage > maxPage) currentPage = maxPage;
      updatePager(filtered.length);
    }

    searchInput.addEventListener("input", function () {
      // Debounce
      if (searchInput._timer) clearTimeout(searchInput._timer);
      searchInput._timer = setTimeout(applyFilters, 200);
    });
    conditionFilter.addEventListener("change", applyFilters);
    clearBtn.addEventListener("click", function () {
      searchInput.value = "";
      conditionFilter.value = "";
      applyFilters();
    });
  }

  /* ── Pager ── */
  function wirePager() {
    document.getElementById("prevPageBtn").addEventListener("click", function () {
      if (currentPage > 0) {
        currentPage--;
        loadTable(currentTable, currentPage);
      }
    });
    document.getElementById("nextPageBtn").addEventListener("click", function () {
      var maxPage = Math.max(0, Math.ceil(allItems.length / pageSize) - 1);
      if (currentPage < maxPage) {
        currentPage++;
        loadTable(currentTable, currentPage);
      }
    });
  }

  function updatePager(filteredLen) {
    var len = filteredLen !== undefined ? filteredLen : allItems.length;
    var maxPage = Math.max(0, Math.ceil(len / pageSize) - 1);
    var info = document.getElementById("pagerInfo");
    info.textContent = len > 0 ? ("Page " + (currentPage + 1) + " of " + (maxPage + 1)) : "No results";
    document.getElementById("prevPageBtn").disabled = (currentPage <= 0);
    document.getElementById("nextPageBtn").disabled = (currentPage >= maxPage);
  }

  /* ── Wire row action buttons (delegated) ── */
  function wireRowActions() {
    var tableBody = document.getElementById("inventoryBody");

    tableBody.addEventListener("click", function (e) {
      var btn = e.target.closest("button");
      if (!btn) return;

      if (btn.classList.contains("lookup-comp-btn")) {
        var id = parseInt(btn.getAttribute("data-id"), 10);
        openCompModal(id);
        return;
      }

      if (btn.classList.contains("edit-item-btn")) {
        var id = parseInt(btn.getAttribute("data-id"), 10);
        wireEditItem(id);
        return;
      }

      if (btn.classList.contains("log-event-btn")) {
        var id = parseInt(btn.getAttribute("data-id"), 10);
        logMemoryEvent(id);
        return;
      }
    });
  }

  /* ── Memory market sidebar ── */
  function loadMemoryMarket() {
    var genCards = document.getElementById("memoryGenCards");
    var eventsBox = document.getElementById("memoryEvents");

    genCards.innerHTML = '<div class="memory-gen-card shimmer" style="height:60px"></div>';
    eventsBox.innerHTML = '<div class="memory-event shimmer" style="height:40px"></div>';

    fetchJSON("/api/memory-market")
      .then(function (data) {
        renderMemoryGenCards(genCards, data.prices || []);
        renderMemoryEvents(eventsBox, data.events || []);
      })
      .catch(function () {
        genCards.innerHTML = '<div class="memory-gen-card"><div class="gen-label">DDR generation spot prices</div><div style="color:var(--text-muted);font-size:0.8rem">Unavailable</div></div>';
      });
  }

  function renderMemoryGenCards(container, prices) {
    var gens = ["DDR3", "DDR4", "DDR5"];
    var html = "";
    gens.forEach(function (gen) {
      var genPrices = prices.filter(function (p) { return p.generation === gen; });
      var avgSpot = null;
      if (genPrices.length) {
        var vals = genPrices.map(function (p) { return p.spot_avg_cents || 0; }).filter(Boolean);
        avgSpot = vals.length ? vals.reduce(function (a, b) { return a + b; }, 0) / vals.length : null;
      }
      var changeHtml = "";
      if (genPrices.length >= 5) {
        var recent = genPrices.slice(0, 5).map(function (p) { return p.spot_avg_cents || 0; });
        var first = recent[recent.length - 1] || 0;
        var last = recent[0] || 0;
        var diff = last - first;
        if (first) {
          var pct = (diff / first * 100);
          changeHtml = '<div class="gen-change ' + (pct >= 0 ? "up" : "down") + '">' +
            (pct >= 0 ? "+" : "") + pct.toFixed(1) + "% (5-period)</div>";
        }
      }
      html += '<div class="memory-gen-card">' +
        '<div class="gen-label">' + gen + '</div>' +
        '<div class="gen-value">' + (avgSpot ? "$" + (avgSpot / 100).toFixed(2) : "—") + '</div>' +
        changeHtml +
        '</div>';
    });
    container.innerHTML = html;
  }

  function renderMemoryEvents(container, events) {
    if (!events.length) {
      container.innerHTML = '<div style="color:var(--text-muted);font-size:0.8rem">No supply-side events logged yet.</div>';
      return;
    }
    container.innerHTML = events.map(function (ev) {
      return '<div class="memory-event">' +
        '<span class="event-type ' + (ev.event_type || "") + '">' + (ev.event_type || "event") + '</span>' +
        '<span class="event-headline">' + escapeHtml(ev.headline || "") + '</span>' +
        (ev.body ? '<div style="color:var(--text-muted);font-size:0.75rem;margin-top:0.15rem">' + escapeHtml(ev.body) + '</div>' : "") +
        (ev.observed_at ? '<span class="event-date">' + formatDate(ev.observed_at) + '</span>' : "") +
        "</div>";
    }).join("");
  }

  function logMemoryEvent(itemId) {
    // Quick inline log: prompt for event type + headline, write to memory_events.
    var eventType = prompt("Event type (tariff, fab_capacity, price_spike, price_drop):", "price_spike");
    if (!eventType) return;
    var headline = prompt("Short headline:", "DDR4 spot up");
    if (!headline) return;
    var body = prompt("Optional detail:", "");
    fetchJSON("/api/memory-market", {
      method: "POST",
      body: JSON.stringify({
        event_type: eventType,
        generation: "DDR4",
        headline: headline,
        body: body || null,
      }),
    })
      .then(function () {
        toast("Event logged", "success");
        loadMemoryMarket();
      })
      .catch(function (err) {
        toast("Failed to log event: " + err.message, "error");
      });
  }

  /* ── Utilities ── */
  function fmtMoney(dollars) {
    if (dollars === null || dollars === undefined || isNaN(dollars)) return "—";
    return "$" + dollars.toFixed(2);
  }

  function formatDate(iso) {
    if (!iso) return "—";
    try {
      var d = new Date(iso);
      if (isNaN(d.getTime())) return iso;
      var now = new Date();
      var diff = now - d;
      if (diff < 60000) return "just now";
      if (diff < 3600000) return Math.floor(diff / 60000) + "m ago";
      if (diff < 86400000) return Math.floor(diff / 3600000) + "h ago";
      return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
    } catch (e) {
      return iso;
    }
  }

  function escapeHtml(s) {
    if (s === null || s === undefined) return "";
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  /* ── Search panel (text + photo) ── */
  function wireSearchPanel() {
    var tabs = document.querySelectorAll("#searchTabs .search-tab");
    var textPanel = document.getElementById("textSearchPanel");
    var photoPanel = document.getElementById("photoSearchPanel");
    var textForm = document.getElementById("textSearchForm");
    var photoForm = document.getElementById("photoSearchForm");
    var photoInput = document.getElementById("photoInput");
    var photoPreview = document.getElementById("photoPreview");
    var photoPlaceholder = document.getElementById("photoPlaceholder");
    var photoDropZone = document.getElementById("photoDropZone");
    var photoBtn = document.getElementById("photoSearchBtn");
    var photoClearBtn = document.getElementById("photoClearBtn");
    var photoStatus = document.getElementById("photoStatus");
    var textResult = document.getElementById("textSearchResult");
    var photoResult = document.getElementById("photoSearchResult");

    // Tab switching
    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        tabs.forEach(function (t) { t.classList.remove("active"); });
        tab.classList.add("active");
        var target = tab.getAttribute("data-tab");
        textPanel.classList.toggle("active", target === "text");
        photoPanel.classList.toggle("active", target === "photo");
      });
    });

    // ── Text search ──
    textForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var input = document.getElementById("textSearchInput");
      var limitInput = document.getElementById("textSearchLimit");
      var q = (input.value || "").trim();
      if (!q) {
        toast("Enter a search query first", "error");
        return;
      }
      var limit = parseInt(limitInput.value, 10) || 20;
      if (limit < 1 || limit > 200) limit = 20;

      var btn = document.getElementById("textSearchBtn");
      var saveBtn = document.getElementById("textSearchSaveBtn");
      btn.disabled = true;
      btn.textContent = "Searching…";
      textResult.hidden = true;

      fetchJSON("/api/search", {
        method: "GET",
        headers: { "Content-Type": "application/json" },
      })
        .then(function () {
          // GET /api/search expects ?q= param; use URL search params
          var params = new URLSearchParams({ q: q, limit: String(limit) });
          return fetchJSON("/api/search?" + params.toString());
        })
        .then(function (data) {
          renderTextSearchResult(data);
          textResult.hidden = false;
          var saveBtn = document.getElementById("textSearchSaveBtn");
          if (saveBtn) {
            saveBtn.hidden = false;
            saveBtn.dataset.query = q;
            saveBtn.dataset.limit = String(limit);
          }
          toast("Found " + (data.sold_count || 0) + " sold + " + (data.active_count || 0) + " active comps", "success");
        })
        .catch(function (err) {
          toast("Search failed: " + err.message, "error");
        })
        .finally(function () {
          btn.disabled = false;
          btn.textContent = "Search comps";
        });
    });

    // Text search save-as-item button (wired once result renders)
    var textSaveBtnObserver = new MutationObserver(function () {
      var btn = document.getElementById("textSearchSaveBtn");
      if (btn && btn._wired) return;
      if (btn) {
        btn._wired = true;
        btn.addEventListener("click", function () {
          var q = this.dataset.query || "";
          var limit = parseInt(this.dataset.limit, 10) || 20;
          if (!q) { toast("No search query to save", "error"); return; }
          openSaveIdentificationModal(null, null, q, limit, "text");
        });
      }
    });
    textSaveBtnObserver.observe(document.getElementById("textSearchResult"), { childList: true, subtree: false });

    // ── Photo search ──
    // Click-to-browse
    photoPlaceholder.addEventListener("click", function () {
      photoInput.click();
    });

    // Drag and drop
    photoDropZone.addEventListener("dragover", function (e) {
      e.preventDefault();
      photoDropZone.classList.add("dragover");
    });
    photoDropZone.addEventListener("dragleave", function () {
      photoDropZone.classList.remove("dragover");
    });
    photoDropZone.addEventListener("drop", function (e) {
      e.preventDefault();
      photoDropZone.classList.remove("dragover");
      var files = e.dataTransfer.files;
      if (files.length) handlePhotoFile(files[0]);
    });

    // File input
    photoInput.addEventListener("change", function () {
      if (photoInput.files.length) handlePhotoFile(photoInput.files[0]);
    });

    // Photo form submit
    photoForm.addEventListener("submit", function (e) {
      e.preventDefault();
      if (!photoPreview.src || photoPreview.src === window.URL.createObjectURL(new File([], ""))) {
        toast("Select an image first", "error");
        return;
      }
      var btn = document.getElementById("photoSearchBtn");
      btn.disabled = true;
      btn.textContent = "Identifying…";
      photoResult.hidden = true;
      photoStatus.textContent = "Sending to Claude for identification…";

      var imageB64 = photoPreview.dataset.b64 || "";
      if (!imageB64) {
        toast("Image data missing — re-select the photo", "error");
        btn.disabled = false;
        btn.textContent = "Identify + search comps";
        return;
      }

      var limit = 20;

      fetchJSON("/api/search/photo", {
        method: "POST",
        body: JSON.stringify({
          image_b64: imageB64,
          image_mime: "image/jpeg",
          limit: limit,
        }),
      })
        .then(function (data) {
          renderPhotoSearchResult(data);
          photoResult.hidden = false;
          photoStatus.textContent = "";
          var photoSaveBtn = document.getElementById("photoSaveBtn");
          if (photoSaveBtn) {
            photoSaveBtn.hidden = false;
            photoSaveBtn.dataset.b64 = imageB64;
            photoSaveBtn.dataset.mime = "image/jpeg";
          }
          toast("Identification complete — " + (data.comps.sold_count || 0) + " sold comps found", "success");
        })
        .catch(function (err) {
          photoStatus.textContent = "";
          toast("Photo search failed: " + err.message, "error");
        })
        .finally(function () {
          btn.disabled = false;
          btn.textContent = "Identify + search comps";
        });
    });

    // Photo clear
    photoClearBtn.addEventListener("click", function () {
      photoInput.value = "";
      photoPreview.src = "";
      photoPreview.dataset.b64 = "";
      photoPreview.hidden = true;
      photoPlaceholder.hidden = false;
      photoBtn.disabled = true;
      photoClearBtn.disabled = true;
      photoResult.hidden = true;
      photoResult.innerHTML = "";
      photoStatus.textContent = "";
      var photoSaveBtn = document.getElementById("photoSaveBtn");
      if (photoSaveBtn) photoSaveBtn.hidden = true;
    });

    function handlePhotoFile(file) {
      if (!file.type.match(/^image\/(jpeg|png|webp)$/)) {
        toast("Please select a JPEG, PNG, or WEBP image", "error");
        return;
      }
      if (file.size > 10 * 1024 * 1024) {
        toast("Image too large — max 10MB", "error");
        return;
      }
      var reader = new FileReader();
      reader.onload = function (e) {
        var result = e.target.result;
        if (typeof result === "string") {
          photoPreview.dataset.b64 = result.split(",")[1] || result;
          photoPreview.src = result;
          photoPreview.onload = function () {
            photoPreview.hidden = false;
            photoPlaceholder.hidden = true;
            photoBtn.disabled = false;
            photoClearBtn.disabled = false;
            photoStatus.textContent = file.name + " (" + (file.size / 1024 / 1024).toFixed(1) + " MB)";
          };
        }
      };
      reader.readAsDataURL(file);
    }
  }

  // ── Render text search results ──
  function renderTextSearchResult(data) {
    var el = document.getElementById("textSearchResult");
    var soldAvg = data.sold_avg || 0;
    var activeAvg = data.active_avg || 0;
    var soldCount = data.sold_count || 0;
    var activeCount = data.active_count || 0;

    var comps = (data.sold_items || []).concat(data.active_items || []);
    var itemsHtml = comps.slice(0, 20).map(function (ci) {
      var img = ci.image_url ? '<img src="' + escapeHtml(ci.image_url) + '" alt="" loading="lazy" />' : "";
      return (
        '<div class="comp-item">' +
        img +
        '<div class="comp-item-title">' + escapeHtml(ci.title || "Unknown") + "</div>" +
        '<div class="comp-item-price">' + fmtMoney(ci.price) + "</div>" +
        '<div class="comp-item-condition">' + escapeHtml(ci.condition || "") + "</div>" +
        "</div>"
      );
    }).join("");

    el.innerHTML =
      '<div class="search-result-header">' +
      '<div class="search-result-title">Comps for: ' + escapeHtml(data.query) + "</div>" +
      '<div class="search-result-stats">' +
      '<span class="search-stat"><b>' + fmtMoney(soldAvg) + '</b> sold avg (' + soldCount + ' listings)</span>' +
      '<span class="search-stat"><b>' + fmtMoney(activeAvg) + '</b> active avg (' + activeCount + ' listings)</span>' +
      "</div>" +
      "</div>" +
      '<div class="comp-items">' + itemsHtml + "</div>" +
      '<div class="search-save-row">' +
      '<button class="btn btn-primary" id="textSearchSaveBtn">Save as inventory item</button>' +
      '<span class="search-save-note">Save the top comp as a tracked item</span>' +
      "</div>";

    // Wire the save button inside the rendered result
    document.getElementById("textSearchSaveBtn").addEventListener("click", function () {
      openSaveIdentificationModal(null, null, data.query, 20, "text");
    });
  }

  // ── Render photo search results ──
  function renderPhotoSearchResult(data) {
    var el = document.getElementById("photoSearchResult");
    var id = data.identification || {};
    var comps = data.comps || {};
    var analysis = data.analysis || {};
    var refinedQuery = data.refined_query || "";

    var soldAvg = comps.sold_avg || 0;
    var activeAvg = comps.active_avg || 0;
    var soldCount = comps.sold_count || 0;
    var activeCount = comps.active_count || 0;

    var dealScore = analysis.deal_score || "UNKNOWN";
    var dealClass = "deal-" + (dealScore && dealScore !== "UNKNOWN" ? dealScore.toLowerCase() : "unknown");

    // Category badge
    var catBadge = id.category && id.category !== "Other"
      ? '<span class="id-category">' + escapeHtml(id.category) + "</span>"
      : "";

    // Condition badge
    var condBadge = id.condition && id.condition !== "unknown"
      ? '<span class="id-condition">' + escapeHtml(id.condition) + "</span>"
      : "";

    var compsList = (comps.sold_items || []).concat(comps.active_items || []);
    var itemsHtml = compsList.slice(0, 20).map(function (ci) {
      var img = ci.image_url ? '<img src="' + escapeHtml(ci.image_url) + '" alt="" loading="lazy" />' : "";
      return (
        '<div class="comp-item">' +
        img +
        '<div class="comp-item-title">' + escapeHtml(ci.title || "Unknown") + "</div>" +
        '<div class="comp-item-price">' + fmtMoney(ci.price) + "</div>" +
        '<div class="comp-item-condition">' + escapeHtml(ci.condition || "") + "</div>" +
        "</div>"
      );
    }).join("");

    el.innerHTML =
      '<div class="search-result-header">' +
      '<div class="search-result-title">' + escapeHtml(id.item_name || "Unknown item") + "</div>" +
      '<div class="search-result-sub">' +
      (id.brand ? escapeHtml(id.brand) + " " : "") +
      (id.model ? escapeHtml(id.model) : "") +
      catBadge + condBadge +
      (refinedQuery && refinedQuery !== id.ebay_search_query
        ? '<span class="refined-query-note">Refined query: ' + escapeHtml(refinedQuery) + "</span>"
        : "") +
      "</div>" +
      "</div>" +

      // Identification block
      '<div class="id-block">' +
      '<div class="id-row"><span class="id-label">Confidence</span><span class="id-value">' + (id.confidence || "low") + "</span></div>" +
      (id.capacity_gb ? '<div class="id-row"><span class="id-label">Capacity</span><span class="id-value">' + id.capacity_gb + ' GB</span></div>' : "") +
      (id.speed_mhz ? '<div class="id-row"><span class="id-label">Speed</span><span class="id-value">' + escapeHtml(id.speed_mhz) + "</span></div>" : "") +
      (id.form_factor ? '<div class="id-row"><span class="id-label">Form Factor</span><span class="id-value">' + escapeHtml(id.form_factor) + "</span></div>" : "") +
      (id.era ? '<div class="id-row"><span class="id-label">Era</span><span class="id-value">' + escapeHtml(id.era) + "</span></div>" : "") +
      "</div>" +

      // Comps summary
      '<div class="comp-summary">' +
      '<div class="comp-stat"><div class="comp-stat-label">Sold Avg</div><div class="comp-stat-value">' + fmtMoney(soldAvg) + "</div></div>" +
      '<div class="comp-stat"><div class="comp-stat-label">Active Avg</div><div class="comp-stat-value">' + fmtMoney(activeAvg) + "</div></div>" +
      '<div class="comp-stat"><div class="comp-stat-label">Sold Listings</div><div class="comp-stat-value">' + soldCount + "</div></div>" +
      '<div class="comp-stat"><div class="comp-stat-label">Active Listings</div><div class="comp-stat-value">' + activeCount + "</div></div>" +
      "</div>" +

      // Analysis block (if available)
      (analysis && analysis.deal_score
        ? '<div class="analysis-block">' +
        '<div class="analysis-header"><span class="deal-badge ' + dealClass + '">' + dealScore + "</span> <span class=\"analysis-label\">Pricing Analysis</span></div>" +
        '<div class="analysis-row"><span class="analysis-label">Market Value</span><span class="analysis-value">' + fmtMoney(analysis.market_value_low) + " – " + fmtMoney(analysis.market_value_high) + "</span></div>" +
        (analysis.suggested_list_price ? '<div class="analysis-row"><span class="analysis-label">Suggested List Price</span><span class="analysis-value">' + fmtMoney(analysis.suggested_list_price) + "</span></div>" : "") +
        (analysis.profit_estimate_low !== undefined ? '<div class="analysis-row"><span class="analysis-label">Profit Estimate</span><span class="analysis-value">' + fmtMoney(analysis.profit_estimate_low) + " – " + fmtMoney(analysis.profit_estimate_high) + "</span></div>" : "") +
        (analysis.deal_score_reason ? '<div class="analysis-row"><span class="analysis-label">Why ' + dealScore + "</span><span class=\"analysis-value\">" + escapeHtml(analysis.deal_score_reason) + "</span></div>" : "") +
        (analysis.best_platforms && analysis.best_platforms.length ? '<div class="analysis-row"><span class="analysis-label">Best Platforms</span><span class="analysis-value">' + escapeHtml(analysis.best_platforms.join(", ")) + "</span></div>" : "") +
        (analysis.selling_tips && analysis.selling_tips.length ? '<div class="analysis-row"><span class="analysis-label">Selling Tips</span><span class="analysis-value tips">' + analysis.selling_tips.map(function (t) { return "• " + escapeHtml(t); }).join("<br>") + "</span></div>" : "") +
        (analysis.keywords_for_listing && analysis.keywords_for_listing.length ? '<div class="analysis-row"><span class="analysis-label">Listing Keywords</span><span class="analysis-value keywords">' + analysis.keywords_for_listing.map(function (k) { return escapeHtml(k); }).join(", ") + "</span></div>" : "") +
        (analysis.watch_out_for ? '<div class="analysis-row"><span class="analysis-label">Watch Out For</span><span class="analysis-value">' + escapeHtml(analysis.watch_out_for) + "</span></div>" : "") +
        "</div>"
        : "") +

      // Comps items
      '<div class="comp-items">' + itemsHtml + "</div>" +

      // Save row
      '<div class="search-save-row">' +
      '<button class="btn btn-primary" id="photoSaveBtn">Save as inventory item</button>' +
      '<span class="search-save-note">Save as tracked item with auto-filled identification + comps</span>' +
      "</div>";

    // Wire the photo save button once the result renders (via MutationObserver)
    var photoSaveBtnObserver = new MutationObserver(function () {
      var btn = document.getElementById("photoSaveBtn");
      if (btn && btn._wired) return;
      if (btn) {
        btn._wired = true;
        btn.addEventListener("click", function () {
          openSaveIdentificationModal(
            data.identification,
            data.comps,
            data.analysis,
            20,
            "photo"
          );
        });
      }
    });
    photoSaveBtnObserver.observe(document.getElementById("photoSearchResult"), { childList: true, subtree: false });
  }

  // ── Save identification as inventory item modal ──
  var saveModal = null;

  function openSaveIdentificationModal(identification, comps, analysis, limit, source) {
    if (!saveModal) {
      saveModal = document.createElement("div");
      saveModal.className = "modal-overlay";
      saveModal.id = "saveModal";
      saveModal.innerHTML =
        '<div class="modal">' +
        '<h2>' + (source === "photo" ? "Save Identified Item" : "Save Search Result") + "</h2>" +
        '<p class="subtitle" style="margin-bottom:1rem">' +
        (source === "photo"
          ? "Item identified from photo. Auto-fill identification and comp data, then set acquisition cost to calculate profit."
          : "Save this search result as a tracked inventory item.") +
        "</p>" +
        '<form id="saveForm" class="modal-form" autocomplete="off">' +
        '<input type="hidden" name="source" id="saveSource" />' +
        '<input type="hidden" name="identification" id="saveIdentification" />' +
        '<input type="hidden" name="comps" id="saveComps" />' +
        '<input type="hidden" name="analysis" id="saveAnalysis" />' +
        '<input type="hidden" name="ebay_query" id="saveEbayQuery" />' +
        '<div class="field-row">' +
        '<label class="field"><span>Table Type</span><select name="table_type" id="saveTableType" class="input">' +
        '<option value="goodwill_flips">Goodwill Flips</option>' +
        '<option value="personal_assets">Personal Assets</option>' +
        '<option value="liquidation_pallets">Liquidation Pallets</option>' +
        '<option value="ram_ssd_stockpile">RAM/SSD Stockpile</option>' +
        '<option value="device_farm">Device Farm</option>' +
        "</select></span></div>" +
        '<label class="field"><span>Name</span><input type="text" name="name" id="saveName" class="input" required /></span></div>' +
        '<div class="field-row">' +
        '<label class="field"><span>Make</span><input type="text" name="make" id="saveMake" class="input" /></span></div>' +
        '<label class="field"><span>Model</span><input type="text" name="model" id="saveModel" class="input" /></span></div>' +
        '<div class="field-row">' +
        '<label class="field"><span>Category</span><input type="text" name="category" id="saveCategory" class="input" /></span></div>' +
        '<label class="field"><span>Condition</span><select name="condition" id="saveCondition" class="input">' +
        '<option value="">—</option>' +
        '<option value="like_new">Like New</option>' +
        '<option value="good">Good</option>' +
        '<option value="fair">Fair</option>' +
        '<option value="poor">Poor</option>' +
        "</select></span></div>" +
        '<label class="field"><span>Acquisition Cost ($)</span><input type="number" step="0.01" name="acquisition_cost_cents" id="saveCost" class="input" placeholder="0.00" /></span></div>' +
        '<div class="field-row">' +
        '<label class="field"><span>Buy Source</span><input type="text" name="buy_source" id="saveSourceField" class="input" /></span></div>' +
        '<label class="field"><span>eBay Search Query</span><input type="text" name="search_query" id="saveSearchQuery" class="input" /></span></div>' +
        '<label class="field"><span>Notes</span><textarea name="notes" id="saveNotes" class="input" rows="2"></textarea></span></div>' +
        '<div class="modal-actions">' +
        '<button type="button" class="btn" id="saveCancelBtn">Cancel</button>' +
        '<button type="submit" class="btn btn-primary" id="saveSubmitBtn">Save Item</button>' +
        "</div>" +
        "</form>" +
        '<div id="saveResult" class="comp-result" hidden></div>' +
        "</div>";
      document.body.appendChild(saveModal);
    }

    // Pre-fill from identification/comps if available
    var nameField = document.getElementById("saveName");
    var makeField = document.getElementById("saveMake");
    var modelField = document.getElementById("saveModel");
    var catField = document.getElementById("saveCategory");
    var condField = document.getElementById("saveCondition");
    var costField = document.getElementById("saveCost");
    var sourceField = document.getElementById("saveSourceField");
    var queryField = document.getElementById("saveSearchQuery");
    var notesField = document.getElementById("saveNotes");

    nameField.value = "";
    makeField.value = "";
    modelField.value = "";
    catField.value = "";
    condField.value = "";
    costField.value = "";
    sourceField.value = "";
    queryField.value = "";
    notesField.value = "";

    var ebayQuery = "";
    if (identification) {
      nameField.value = identification.item_name || "";
      makeField.value = identification.brand || "";
      modelField.value = identification.model || "";
      catField.value = identification.category || "";
      condField.value = identification.condition || "";
      ebayQuery = identification.ebay_search_query || identification.item_name || "";
    }
    if (comps) {
      queryField.value = ebayQuery;
    }
    if (source === "photo") {
      sourceField.value = "Photo scan";
      notesField.value = (identification ? ("Identified: " + (identification.item_name || "") + ". ") : "") +
        (analysis && analysis.deal_score ? ("Deal score: " + analysis.deal_score + ". ") : "") +
        (analysis && analysis.watch_out_for ? ("Watch: " + analysis.watch_out_for) : "");
    } else {
      sourceField.value = "eBay comp search";
      notesField.value = "Found via eBay comp search: " + ebayQuery;
    }

    document.getElementById("saveSource").value = source;
    document.getElementById("saveIdentification").value = identification ? JSON.stringify(identification) : "{}";
    document.getElementById("saveComps").value = comps ? JSON.stringify(comps) : "{}";
    document.getElementById("saveAnalysis").value = analysis ? JSON.stringify(analysis) : "{}";
    document.getElementById("saveEbayQuery").value = ebayQuery;

    var modal = document.getElementById("saveModal");
    modal.hidden = false;

    // Wire up once
    if (!saveModal._wired) {
      saveModal._wired = true;
      var form = document.getElementById("saveForm");
      var cancel = document.getElementById("saveCancelBtn");
      var submit = document.getElementById("saveSubmitBtn");

      cancel.addEventListener("click", function () {
        modal.hidden = true;
      });
      modal.addEventListener("click", function (e) {
        if (e.target === modal) modal.hidden = true;
      });

      submit.addEventListener("click", function (e) {
        e.preventDefault();
        var fData = new FormData(form);
        var payload = {
          identification: JSON.parse(document.getElementById("saveIdentification").value || "{}"),
          comps: JSON.parse(document.getElementById("saveComps").value || "{}"),
          analysis: JSON.parse(document.getElementById("saveAnalysis").value || "{}"),
          table_type: fData.get("table_type") || "goodwill_flips",
          acquisition_cost_cents: parseFloat(fData.get("acquisition_cost_cents")) || 0,
          buy_source: fData.get("buy_source") || "Photo scan",
          notes: fData.get("notes") || "",
        };
        var name = fData.get("name") || "Unknown item";
        var make = fData.get("make") || "";
        var model = fData.get("model") || "";
        var category = fData.get("category") || "";
        var condition = fData.get("condition") || "unknown";
        var searchQuery = fData.get("search_query") || (payload.identification.item_name || "");

        payload.identification.item_name = name;
        payload.identification.brand = make;
        payload.identification.model = model;
        payload.identification.category = category;
        payload.identification.condition = condition;
        payload.identification.ebay_search_query = searchQuery;

        submit.disabled = true;
        submit.textContent = "Saving…";
        document.getElementById("saveResult").hidden = true;

        fetchJSON("/api/search/save-identification", {
          method: "POST",
          body: JSON.stringify(payload),
        })
          .then(function (data) {
            var resEl = document.getElementById("saveResult");
            resEl.innerHTML =
              '<div class="search-result-header">' +
              '<div class="search-result-title">Item saved!</div>' +
              '</div>' +
              '<div class="comp-summary">' +
              '<div class="comp-stat"><div class="comp-stat-label">Item ID</div><div class="comp-stat-value">' + data.id + "</div></div>" +
              '<div class="comp-stat"><div class="comp-stat-label">Name</div><div class="comp-stat-value">' + escapeHtml(data.item.name) + "</div></div>" +
              '<div class="comp-stat"><div class="comp-stat-label">Table</div><div class="comp-stat-value">' + data.item.table_type + "</div></div>" +
              "</div>";
            resEl.hidden = false;
            toast("Item saved (ID " + data.id + ")", "success");
            // Reload the table to show the new item
            loadTable(currentTable, 0);
          })
          .catch(function (err) {
            toast("Save failed: " + err.message, "error");
          })
          .finally(function () {
            submit.disabled = false;
            submit.textContent = "Save Item";
          });
      });
    }

    // Focus name field
    setTimeout(function () { nameField.focus(); }, 50);
  }

  // Wire the delegated row-action clicks after the table renderer is defined.
  // (Called from DOMContentLoaded via wireTableRowClicks already above.)
  // Additional init: wire the modal buttons that were defined as separate functions.
  document.addEventListener("DOMContentLoaded", function () {
    wireCompModal();
    wireRowActions();
  });
})();
