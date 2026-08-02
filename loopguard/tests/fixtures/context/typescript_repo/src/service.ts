import User, { Role as UserRole } from "./models";

export function loadUser(id: string): User {
  return { id, role: UserRole.Member } as User;
}

export class UserService {}
