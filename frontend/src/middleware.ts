import { NextResponse, type NextRequest } from "next/server";

// Presence check only: pages render nothing sensitive server-side, and every data request is
// authorised by the backend. This just routes signed-out visitors to the login page.
export function middleware(request: NextRequest) {
  const signedIn = request.cookies.has("sb_session") || request.cookies.has("sb_refresh");
  const onLogin = request.nextUrl.pathname === "/login";
  if (!signedIn && !onLogin) {
    return NextResponse.redirect(new URL("/login", request.url));
  }
  if (signedIn && onLogin) {
    return NextResponse.redirect(new URL("/", request.url));
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!api/|_next/|favicon.ico|robots.txt).*)"],
};
