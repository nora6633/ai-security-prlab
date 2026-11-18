import "./globals.css";

export const metadata = {
  title: "TWISC @ NTUST | AI Security Lab",
  description: "臺科大資通安全研究與教學中心 AI 資安實驗室",
};

export default function RootLayout({ children }) {
  return (
    <html lang="zh-Hant">
      <body>{children}</body>
    </html>
  );
}
