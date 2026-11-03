import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
const root = "../../src/makan/evals/sets/clef/";
const manifest = JSON.parse(readFileSync(root + "manifest.json", "utf8"));
for (const name of ["contract.json", "state.schema.json"]) {
  const hash = createHash("sha256")
    .update(readFileSync(root + name))
    .digest("hex");
  if (hash !== manifest.files["src/makan/evals/sets/clef/" + name]) {
    throw new Error(
      "Frozen Clef contract changed; qualify and version before use",
    );
  }
}

if (
  !readFileSync("src/config.ts", "utf8").includes(
    `"${manifest.content_sha256}"`,
  )
) {
  throw new Error("Gateway must pin the frozen manifest content hash");
}
