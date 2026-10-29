import type { Metadata } from "next";
import { Libre_Franklin, Manrope } from "next/font/google";
import "./globals.css";

const body = Manrope({ variable: "--font-body", subsets: ["latin"], display: "swap" });
const heading = Libre_Franklin({ variable: "--font-heading", subsets: ["latin"], display: "swap" });

export const metadata: Metadata = {
  title: "Sightbridge Advisor Intelligence",
  description: "Trusted answers for tax-advantaged real estate.",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${body.variable} ${heading.variable}`}>
      <body className="antialiased">{children}</body>
    </html>
  );
}
