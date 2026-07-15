import { loadUser } from "./service";

describe("loadUser", () => {
  test("loads a user", () => loadUser("1"));
});
