// Prototype data for the bid scene (shiroebi, 1 box = 1 kg, yen, tax excluded).
// All organisations, conditions and values are fictional.
// This file is temporary: the values will come from the Agent Dock database
// (market history, own trades, sale prices, costs) once the data layer exists.
window.MOCK_BID = {
  item: "シロエビ",
  unit: "1箱（1kg）",
  scale: { min: 3400, max: 5400 },
  lastYear: { low: 3600, high: 4300, median: 3950 },
  recent: { low: 3800, high: 4500, median: 4150 },
  computedLimit: { salePrice: 6900, processing: 1400, profit: 500, value: 5000 },
  plan: { low: 3900, limit: 4800, boxes: 8, approvedBy: "網谷さん" },
  choice: { price: 4300, boxes: 5, reason: "今朝の水揚げは少なめ。むき身の注文の5箱を確実に取る" },
  book: {
    own: [
      { tag: "決まり", text: "計画の上限の中で、いつもの量を確実に取る" },
      { tag: "決まり", text: "むき身の注文の分は先に確保する" },
      { tag: "これまでの例", text: "2025-11-08 水揚げ少なめの日、4,250円・5箱で確保" }
    ],
    data: [
      { tag: "相場", text: "直近30日の中央値 4,150円（幅 3,800〜4,500円）", to: "recent" },
      { tag: "相場", text: "去年の同じ時期の中央値 3,950円（幅 3,600〜4,300円）", to: "lastYear" },
      { tag: "注文", text: "むき身の注文 5kg（量販・明日まで）", to: "mark" },
      { tag: "状況", text: "今朝の水揚げは20箱（いつもより少なめ）", to: "mark" }
    ]
  }
};
