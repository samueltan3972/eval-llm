// Bridge for promptfoo-style `javascript` assertions.
// Reads {"code": "<assertion body>", "output": "<model output>"} as JSON on stdin and
// prints {"pass", "score", "reason"} as JSON on stdout. The assertion body runs with
// `output` in scope and returns either a boolean or {pass, score, reason} — matching the
// contract used in datasets/*.yaml, so those cases (incl. code-gen that executes generated
// code) run unchanged.
let input = "";
process.stdin.on("data", (d) => (input += d));
process.stdin.on("end", () => {
  let result;
  try {
    const { code, output } = JSON.parse(input);
    const fn = new Function("output", code);
    let r = fn(output);
    if (typeof r === "boolean") {
      result = { pass: r, score: r ? 1 : 0, reason: r ? "passed" : "failed" };
    } else if (r && typeof r === "object") {
      const score = typeof r.score === "number" ? r.score : r.pass ? 1 : 0;
      result = { pass: !!r.pass, score, reason: r.reason || "" };
    } else {
      result = { pass: false, score: 0, reason: "assertion returned neither boolean nor object" };
    }
  } catch (e) {
    result = { pass: false, score: 0, reason: "js error: " + (e && e.message ? e.message : String(e)) };
  }
  process.stdout.write(JSON.stringify(result));
});
