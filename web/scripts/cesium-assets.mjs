import { cp, mkdir } from "node:fs/promises";
import { resolve } from "node:path";
const destination = resolve("public/cesium");
await mkdir(destination, { recursive: true });
for (const name of ["Workers", "ThirdParty", "Assets", "Widgets"])
  await cp(
    resolve("node_modules/cesium/Build/Cesium", name),
    resolve(destination, name),
    { recursive: true },
  );
