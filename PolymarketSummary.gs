/**
 * Polymarket Trade Summary Script
 * Aggregates trades by WindowID and calculates P&L from resolutions
 */

const ACTIVITY_SHEET_NAME = "Activity Log";
const SUMMARY_SHEET_NAME = "Summary";
const POLYMARKET_API_BASE = "https://clob.polymarket.com/markets/";

/**
 * Main function to process trades and update summary
 */
function processTradesAndUpdateSummary() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const activitySheet = ss.getSheetByName(ACTIVITY_SHEET_NAME);
  const summarySheet = getOrCreateSummarySheet(ss);

  if (!activitySheet) {
    throw new Error(`Sheet "${ACTIVITY_SHEET_NAME}" not found`);
  }

  // Read and filter trades
  const trades = readAndFilterTrades(activitySheet);
  Logger.log(`Found ${trades.length} TRADE entries`);

  // Aggregate by WindowID
  const aggregated = aggregateByWindowId(trades);
  Logger.log(`Aggregated into ${Object.keys(aggregated).length} windows`);

  // Get existing summary data for deduplication
  const existingSummary = getExistingSummaryData(summarySheet);

  // Process each window
  const results = [];
  for (const windowId in aggregated) {
    const windowData = aggregated[windowId];

    // Get resolution from API
    const resolution = getMarketResolution(windowData.conditionId);

    // Calculate P&L
    const pnl = calculatePnL(windowData, resolution);

    results.push({
      windowId: windowId,
      ...windowData,
      ...pnl,
      resolution: resolution
    });
  }

  // Write to summary sheet (with deduplication)
  writeSummary(summarySheet, results, existingSummary);

  Logger.log("Summary updated successfully");
}

/**
 * Read trades from Activity Log and filter for TRADE type only
 */
function readAndFilterTrades(sheet) {
  const data = sheet.getDataRange().getValues();
  const headers = data[0];

  // Find column indices
  const cols = {
    type: headers.indexOf("type"),
    slug: headers.indexOf("slug"),
    outcome: headers.indexOf("outcome"),
    size: headers.indexOf("size"),
    usdcSize: headers.indexOf("usdcSize"),
    price: headers.indexOf("price"),
    conditionId: headers.indexOf("conditionId")
  };

  // Validate required columns exist
  for (const [name, idx] of Object.entries(cols)) {
    if (idx === -1) {
      throw new Error(`Required column "${name}" not found in Activity Log`);
    }
  }

  const trades = [];
  for (let i = 1; i < data.length; i++) {
    const row = data[i];

    // Filter for TRADE type only
    if (row[cols.type] !== "TRADE") {
      continue;
    }

    // Extract WindowID from slug (last number after btc-updown-15m-)
    const slug = row[cols.slug] || "";
    const windowId = extractWindowId(slug);

    if (!windowId) {
      Logger.log(`Skipping row ${i + 1}: Could not extract WindowID from slug "${slug}"`);
      continue;
    }

    trades.push({
      windowId: windowId,
      outcome: row[cols.outcome],
      size: parseFloat(row[cols.size]) || 0,
      usdcSize: parseFloat(row[cols.usdcSize]) || 0,
      price: parseFloat(row[cols.price]) || 0,
      conditionId: row[cols.conditionId]
    });
  }

  return trades;
}

/**
 * Extract WindowID from slug (e.g., "btc-updown-15m-12345" -> "12345")
 */
function extractWindowId(slug) {
  // Match the last number after btc-updown-15m-
  const match = slug.match(/btc-updown-15m-(\d+)/i);
  if (match) {
    return match[1];
  }

  // Fallback: try to get last number segment
  const parts = slug.split("-");
  const lastPart = parts[parts.length - 1];
  if (/^\d+$/.test(lastPart)) {
    return lastPart;
  }

  return null;
}

/**
 * Aggregate trades by WindowID
 */
function aggregateByWindowId(trades) {
  const aggregated = {};

  for (const trade of trades) {
    const wid = trade.windowId;

    if (!aggregated[wid]) {
      aggregated[wid] = {
        upShares: 0,
        upTotal: 0,
        upPriceSum: 0,
        upCount: 0,
        downShares: 0,
        downTotal: 0,
        downPriceSum: 0,
        downCount: 0,
        conditionId: trade.conditionId
      };
    }

    const agg = aggregated[wid];

    // Store conditionId (grab from any trade)
    if (!agg.conditionId && trade.conditionId) {
      agg.conditionId = trade.conditionId;
    }

    if (trade.outcome === "Up") {
      agg.upShares += trade.size;
      agg.upTotal += trade.usdcSize;
      agg.upPriceSum += trade.price;
      agg.upCount++;
    } else if (trade.outcome === "Down") {
      agg.downShares += trade.size;
      agg.downTotal += trade.usdcSize;
      agg.downPriceSum += trade.price;
      agg.downCount++;
    }
  }

  // Calculate averages and totals
  for (const wid in aggregated) {
    const agg = aggregated[wid];

    agg.upPrice = agg.upCount > 0 ? agg.upPriceSum / agg.upCount : 0;
    agg.downPrice = agg.downCount > 0 ? agg.downPriceSum / agg.downCount : 0;
    agg.totalBet = agg.upTotal + agg.downTotal;
    agg.totalShares = agg.upShares + agg.downShares;
    agg.combinedPrice = agg.totalShares > 0 ? agg.totalBet / agg.totalShares : 0;

    // Clean up temp fields
    delete agg.upPriceSum;
    delete agg.upCount;
    delete agg.downPriceSum;
    delete agg.downCount;
  }

  return aggregated;
}

/**
 * Get market resolution from Polymarket API
 */
function getMarketResolution(conditionId) {
  if (!conditionId) {
    return { winner: null, resolved: false };
  }

  try {
    const url = POLYMARKET_API_BASE + conditionId;
    const response = UrlFetchApp.fetch(url, {
      method: "GET",
      muteHttpExceptions: true
    });

    const code = response.getResponseCode();
    if (code !== 200) {
      Logger.log(`API error for ${conditionId}: ${code}`);
      return { winner: null, resolved: false };
    }

    const data = JSON.parse(response.getContentText());

    // Check tokens array for winner
    if (data.tokens && Array.isArray(data.tokens)) {
      for (const token of data.tokens) {
        if (token.winner === true || token.winner === "true") {
          return {
            winner: token.outcome,
            resolved: true
          };
        }
      }
    }

    return { winner: null, resolved: false };

  } catch (e) {
    Logger.log(`Error fetching resolution for ${conditionId}: ${e.message}`);
    return { winner: null, resolved: false };
  }
}

/**
 * Calculate P&L based on resolution
 */
function calculatePnL(windowData, resolution) {
  const result = {
    payout: 0,
    profitLoss: 0,
    profitLossPct: 0,
    winner: resolution.winner,
    resolved: resolution.resolved
  };

  if (!resolution.resolved || !resolution.winner) {
    return result;
  }

  // Payout is the shares of the winning side
  if (resolution.winner === "Up") {
    result.payout = windowData.upShares;
  } else if (resolution.winner === "Down") {
    result.payout = windowData.downShares;
  }

  result.profitLoss = result.payout - windowData.totalBet;
  result.profitLossPct = windowData.totalBet > 0
    ? result.profitLoss / windowData.totalBet
    : 0;

  return result;
}

/**
 * Get or create Summary sheet with headers
 */
function getOrCreateSummarySheet(ss) {
  let sheet = ss.getSheetByName(SUMMARY_SHEET_NAME);

  if (!sheet) {
    sheet = ss.insertSheet(SUMMARY_SHEET_NAME);

    // Add headers
    const headers = [
      "WindowID",
      "ConditionID",
      "UpShares",
      "UpTotal",
      "UpPrice",
      "DownShares",
      "DownTotal",
      "DownPrice",
      "TotalBet",
      "CombinedPrice",
      "Resolved",
      "Winner",
      "Payout",
      "ProfitLoss",
      "ProfitLossPct"
    ];

    sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
    sheet.getRange(1, 1, 1, headers.length).setFontWeight("bold");
    sheet.setFrozenRows(1);
  }

  return sheet;
}

/**
 * Get existing summary data for deduplication
 */
function getExistingSummaryData(sheet) {
  const data = sheet.getDataRange().getValues();
  const existing = {};

  // Skip header row
  for (let i = 1; i < data.length; i++) {
    const windowId = data[i][0];
    if (windowId) {
      existing[windowId] = {
        row: i + 1,  // 1-indexed for sheet operations
        resolved: data[i][10]
      };
    }
  }

  return existing;
}

/**
 * Write results to Summary sheet with deduplication
 */
function writeSummary(sheet, results, existingSummary) {
  for (const result of results) {
    const existing = existingSummary[result.windowId];

    const rowData = [
      result.windowId,
      result.conditionId,
      result.upShares,
      result.upTotal,
      result.upPrice,
      result.downShares,
      result.downTotal,
      result.downPrice,
      result.totalBet,
      result.combinedPrice,
      result.resolved,
      result.winner || "",
      result.payout,
      result.profitLoss,
      result.profitLossPct
    ];

    if (existing) {
      // Update existing row if now resolved (was unresolved before)
      if (result.resolved && !existing.resolved) {
        sheet.getRange(existing.row, 1, 1, rowData.length).setValues([rowData]);
        Logger.log(`Updated WindowID ${result.windowId} with resolution`);
      }
    } else {
      // Add new row
      sheet.appendRow(rowData);
      Logger.log(`Added new WindowID ${result.windowId}`);
    }
  }

  // Format percentage column
  formatSummarySheet(sheet);
}

/**
 * Apply formatting to Summary sheet
 */
function formatSummarySheet(sheet) {
  const lastRow = sheet.getLastRow();
  if (lastRow <= 1) return;

  // Format currency columns (UpTotal, DownTotal, TotalBet, Payout, ProfitLoss)
  const currencyCols = [4, 7, 9, 13, 14];
  for (const col of currencyCols) {
    sheet.getRange(2, col, lastRow - 1, 1).setNumberFormat("$#,##0.00");
  }

  // Format price columns
  const priceCols = [5, 8, 10];
  for (const col of priceCols) {
    sheet.getRange(2, col, lastRow - 1, 1).setNumberFormat("0.0000");
  }

  // Format percentage column
  sheet.getRange(2, 15, lastRow - 1, 1).setNumberFormat("0.00%");

  // Format shares columns
  const sharesCols = [3, 6];
  for (const col of sharesCols) {
    sheet.getRange(2, col, lastRow - 1, 1).setNumberFormat("#,##0.00");
  }
}

/**
 * Menu item to run manually
 */
function onOpen() {
  const ui = SpreadsheetApp.getUi();
  ui.createMenu("Polymarket")
    .addItem("Update Summary", "processTradesAndUpdateSummary")
    .addItem("Refresh Resolutions Only", "refreshUnresolvedMarkets")
    .addToUi();
}

/**
 * Refresh only unresolved markets (faster update)
 */
function refreshUnresolvedMarkets() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const summarySheet = ss.getSheetByName(SUMMARY_SHEET_NAME);

  if (!summarySheet) {
    Logger.log("Summary sheet not found");
    return;
  }

  const data = summarySheet.getDataRange().getValues();
  let updated = 0;

  for (let i = 1; i < data.length; i++) {
    const resolved = data[i][10];
    const conditionId = data[i][1];

    if (!resolved && conditionId) {
      const resolution = getMarketResolution(conditionId);

      if (resolution.resolved) {
        const windowData = {
          upShares: data[i][2],
          downShares: data[i][5],
          totalBet: data[i][8]
        };

        const pnl = calculatePnL(windowData, resolution);

        // Update resolution columns
        summarySheet.getRange(i + 1, 11).setValue(true);
        summarySheet.getRange(i + 1, 12).setValue(resolution.winner);
        summarySheet.getRange(i + 1, 13).setValue(pnl.payout);
        summarySheet.getRange(i + 1, 14).setValue(pnl.profitLoss);
        summarySheet.getRange(i + 1, 15).setValue(pnl.profitLossPct);

        updated++;
        Logger.log(`Resolved WindowID ${data[i][0]}: ${resolution.winner}`);
      }
    }

    // Rate limiting for API calls
    Utilities.sleep(100);
  }

  Logger.log(`Updated ${updated} newly resolved markets`);
}

/**
 * Trigger setup for automatic updates (run once)
 */
function setupTrigger() {
  // Remove existing triggers
  const triggers = ScriptApp.getProjectTriggers();
  for (const trigger of triggers) {
    if (trigger.getHandlerFunction() === "refreshUnresolvedMarkets") {
      ScriptApp.deleteTrigger(trigger);
    }
  }

  // Create new trigger - runs every 15 minutes
  ScriptApp.newTrigger("refreshUnresolvedMarkets")
    .timeBased()
    .everyMinutes(15)
    .create();

  Logger.log("Trigger set up to run every 15 minutes");
}
