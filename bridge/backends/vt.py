"""Small VT100/xterm screen emulator for the in-game terminal.

Covers what shells, ConPTY, git, npm, python REPLs, htop-like and simple full-screen
tools print: cursor movement, erase, insert/delete, scroll regions, SGR colors
(16/256/truecolor, bold, inverse), alternate screen, OSC titles. Unknown sequences are
ignored. Output for the client: rows of runs [text, fg, bg, bold] with 0xRRGGBB colors
(None = default).
"""

BASE16 = [0x0C0C0C, 0xC50F1F, 0x13A10E, 0xC19C00, 0x0037DA, 0x881798, 0x3A96DD, 0xCCCCCC,
          0x767676, 0xE74856, 0x16C60C, 0xF9F1A5, 0x3B78FF, 0xB4009E, 0x61D6D6, 0xF2F2F2]


def color256(n):
    if n < 16:
        return BASE16[n]
    if n < 232:
        n -= 16
        steps = [0, 95, 135, 175, 215, 255]
        return (steps[n // 36] << 16) | (steps[(n // 6) % 6] << 8) | steps[n % 6]
    v = 8 + (n - 232) * 10
    return (v << 16) | (v << 8) | v


BLANK = (' ', None, None, False)


class Screen:
    def __init__(self, cols=80, rows=24, scrollback=2000, reply=None):
        self.cols, self.rows = cols, rows
        self.limit = scrollback
        self.reply = reply or (lambda data: None)  # answers to queries like ESC[6n
        self.history = []
        self.title = ''
        self.version = 0
        self._reset_state()
        self.grid = self._blank_grid()
        self.saved_main = None
        self.mode = 'normal'
        self.buf = ''

    # ---- state ------------------------------------------------------------
    def _reset_state(self):
        self.x = self.y = 0
        self.fg = self.bg = None
        self.bold = self.inverse = False
        self.top, self.bottom = 0, self.rows - 1
        self.wrap_next = False
        self.cursor_visible = True
        self.saved = (0, 0)

    def _blank_row(self):
        return [BLANK] * self.cols

    def _blank_grid(self):
        return [self._blank_row() for _ in range(self.rows)]

    def resize(self, cols, rows):
        cols, rows = max(10, min(cols, 400)), max(3, min(rows, 200))
        if (cols, rows) == (self.cols, self.rows):
            return
        grid = []
        for row in self.grid:
            row = row[:cols] + [BLANK] * max(0, cols - len(row))
            grid.append(row)
        # keep the bottom part (where the prompt is) when the window gets shorter
        while len(grid) > rows:
            if self.y > 0:
                self._push_history(grid.pop(0))
                self.y -= 1
            else:
                grid.pop()
        while len(grid) < rows:
            grid.append([BLANK] * cols)
        self.grid, self.cols, self.rows = grid, cols, rows
        self.top, self.bottom = 0, rows - 1
        self.x, self.y = min(self.x, cols - 1), min(self.y, rows - 1)
        self.version += 1

    def _push_history(self, row):
        if self.saved_main is None:  # the alternate screen has no scrollback
            self.history.append(row)
            if len(self.history) > self.limit:
                del self.history[:len(self.history) - self.limit]

    # ---- output -------------------------------------------------------------
    def _style(self):
        fg, bg = (self.bg, self.fg) if self.inverse else (self.fg, self.bg)
        if self.inverse:
            fg = fg if fg is not None else 0x0C0C0C
            bg = bg if bg is not None else 0xCCCCCC
        return fg, bg, self.bold

    def _put(self, ch):
        if self.wrap_next:
            self.x = 0
            self._linefeed()
            self.wrap_next = False
        fg, bg, bold = self._style()
        self.grid[self.y][self.x] = (ch, fg, bg, bold)
        if self.x == self.cols - 1:
            self.wrap_next = True
        else:
            self.x += 1

    def _linefeed(self):
        if self.y == self.bottom:
            self._scroll_up(1)
        elif self.y < self.rows - 1:
            self.y += 1

    def _scroll_up(self, n):
        for _ in range(n):
            row = self.grid.pop(self.top)
            if self.top == 0:
                self._push_history(row)
            self.grid.insert(self.bottom, self._blank_row())

    def _scroll_down(self, n):
        for _ in range(n):
            self.grid.pop(self.bottom)
            self.grid.insert(self.top, self._blank_row())

    def feed(self, text):
        for ch in text:
            self._char(ch)
        self.version += 1

    def _char(self, ch):
        mode = self.mode
        if mode == 'normal':
            o = ord(ch)
            if o >= 32 and o != 127:
                self._put(ch)
            elif ch == '\x1b':
                self.mode, self.buf = 'esc', ''
            elif ch == '\r':
                self.x, self.wrap_next = 0, False
            elif ch in '\n\x0b\x0c':
                self._linefeed()
                self.wrap_next = False
            elif ch == '\b':
                self.x, self.wrap_next = max(0, self.x - 1), False
            elif ch == '\t':
                self.x = min(self.cols - 1, (self.x // 8 + 1) * 8)
        elif mode == 'esc':
            if ch == '[':
                self.mode, self.buf = 'csi', ''
            elif ch == ']':
                self.mode, self.buf = 'osc', ''
            elif ch in '()*+':
                self.mode = 'charset'
            else:
                self.mode = 'normal'
                if ch == '7':
                    self.saved = (self.x, self.y)
                elif ch == '8':
                    self.x, self.y = self.saved
                elif ch == 'M':  # reverse index
                    if self.y == self.top:
                        self._scroll_down(1)
                    elif self.y > 0:
                        self.y -= 1
                elif ch == 'D':
                    self._linefeed()
                elif ch == 'E':
                    self.x = 0
                    self._linefeed()
                elif ch == 'c':
                    self._reset_state()
                    self.grid = self._blank_grid()
        elif mode == 'charset':
            self.mode = 'normal'
        elif mode == 'osc':
            if ch == '\x07' or (ch == '\\' and self.buf.endswith('\x1b')):
                body = self.buf.rstrip('\x1b')
                if body[:2] in ('0;', '2;'):
                    self.title = body[2:][:200]
                self.mode = 'normal'
            else:
                self.buf += ch
                if len(self.buf) > 4096:
                    self.mode = 'normal'
        elif mode == 'csi':
            if '@' <= ch <= '~':
                self.mode = 'normal'
                self._csi(self.buf, ch)
            else:
                self.buf += ch
                if len(self.buf) > 64:
                    self.mode = 'normal'

    def _csi(self, params, final):
        private = params[:1] in ('?', '>', '=', '<')
        body = params[1:] if private else params
        try:
            args = [int(p) if p else 0 for p in body.split(';')] if body else []
        except ValueError:
            args = []

        def arg(i=0, default=1):
            return args[i] if len(args) > i and args[i] else default

        if private:
            if final in 'hl' and params[0] == '?':
                on = final == 'h'
                for a in args:
                    if a == 25:
                        self.cursor_visible = on
                    elif a in (47, 1047, 1049):
                        self._alt(on)
            return
        self.wrap_next = False
        if final == 'A':
            self.y = max(self.top if self.y >= self.top else 0, self.y - arg())
        elif final == 'B':
            self.y = min(self.bottom if self.y <= self.bottom else self.rows - 1, self.y + arg())
        elif final in 'Ca':
            self.x = min(self.cols - 1, self.x + arg())
        elif final == 'D':
            self.x = max(0, self.x - arg())
        elif final == 'E':
            self.x, self.y = 0, min(self.rows - 1, self.y + arg())
        elif final == 'F':
            self.x, self.y = 0, max(0, self.y - arg())
        elif final in 'G`':
            self.x = min(self.cols - 1, arg() - 1)
        elif final in 'Hf':
            self.y = min(self.rows - 1, arg(0) - 1)
            self.x = min(self.cols - 1, arg(1) - 1)
        elif final == 'd':
            self.y = min(self.rows - 1, arg() - 1)
        elif final == 'J':
            self._erase_display(args[0] if args else 0)
        elif final == 'K':
            self._erase_line(args[0] if args else 0)
        elif final == 'X':
            n = arg()
            row = self.grid[self.y]
            for i in range(self.x, min(self.cols, self.x + n)):
                row[i] = BLANK
        elif final == 'P':
            row = self.grid[self.y]
            n = min(arg(), self.cols - self.x)
            del row[self.x:self.x + n]
            row.extend([BLANK] * n)
        elif final == '@':
            row = self.grid[self.y]
            n = min(arg(), self.cols - self.x)
            row[self.x:self.x] = [BLANK] * n
            del row[self.cols:]
        elif final == 'L':
            if self.top <= self.y <= self.bottom:
                for _ in range(arg()):
                    self.grid.pop(self.bottom)
                    self.grid.insert(self.y, self._blank_row())
        elif final == 'M':
            if self.top <= self.y <= self.bottom:
                for _ in range(arg()):
                    self.grid.pop(self.y)
                    self.grid.insert(self.bottom, self._blank_row())
        elif final == 'S':
            self._scroll_up(arg())
        elif final == 'T':
            self._scroll_down(arg())
        elif final == 'r':
            top, bottom = arg(0) - 1, arg(1, self.rows) - 1
            if 0 <= top < bottom < self.rows:
                self.top, self.bottom = top, bottom
                self.x = self.y = 0
        elif final == 'm':
            self._sgr(args or [0])
        elif final == 's':
            self.saved = (self.x, self.y)
        elif final == 'u':
            self.x, self.y = self.saved
        elif final == 'n' and args[:1] == [6]:
            self.reply(f'\x1b[{self.y + 1};{self.x + 1}R')
        elif final == 'c':
            self.reply('\x1b[?1;0c')

    def _alt(self, on):
        if on and self.saved_main is None:
            self.saved_main = (self.grid, self.x, self.y)
            self.grid = self._blank_grid()
            self.x = self.y = 0
        elif not on and self.saved_main is not None:
            self.grid, self.x, self.y = self.saved_main
            self.saved_main = None
            self.x, self.y = min(self.x, self.cols - 1), min(self.y, self.rows - 1)

    def _erase_display(self, how):
        if how == 0:
            self._erase_line(0)
            for r in range(self.y + 1, self.rows):
                self.grid[r] = self._blank_row()
        elif how == 1:
            self._erase_line(1)
            for r in range(0, self.y):
                self.grid[r] = self._blank_row()
        elif how in (2, 3):
            # ConPTY repaints with ESC[2J after every resize, so the screen is not copied
            # into the scrollback here (that would duplicate it).
            if how == 3:
                self.history.clear()
            self.grid = self._blank_grid()

    def _erase_line(self, how):
        row = self.grid[self.y]
        if how == 0:
            start, end = self.x, self.cols
        elif how == 1:
            start, end = 0, self.x + 1
        else:
            start, end = 0, self.cols
        for i in range(start, min(end, self.cols)):
            row[i] = BLANK

    def _sgr(self, args):
        i = 0
        while i < len(args):
            a = args[i]
            if a == 0:
                self.fg = self.bg = None
                self.bold = self.inverse = False
            elif a == 1:
                self.bold = True
            elif a == 22:
                self.bold = False
            elif a == 7:
                self.inverse = True
            elif a == 27:
                self.inverse = False
            elif 30 <= a <= 37:
                self.fg = BASE16[a - 30]
            elif 90 <= a <= 97:
                self.fg = BASE16[a - 90 + 8]
            elif 40 <= a <= 47:
                self.bg = BASE16[a - 40]
            elif 100 <= a <= 107:
                self.bg = BASE16[a - 100 + 8]
            elif a == 39:
                self.fg = None
            elif a == 49:
                self.bg = None
            elif a in (38, 48) and i + 1 < len(args):
                value = None
                if args[i + 1] == 5 and i + 2 < len(args):
                    value = color256(args[i + 2] & 255)
                    i += 2
                elif args[i + 1] == 2 and i + 4 < len(args):
                    r, g, b = (x & 255 for x in args[i + 2:i + 5])
                    value = (r << 16) | (g << 8) | b
                    i += 4
                if a == 38:
                    self.fg = value
                else:
                    self.bg = value
            i += 1

    # ---- export ---------------------------------------------------------------
    @staticmethod
    def runs(row):
        out = []
        for ch, fg, bg, bold in row:
            if out and out[-1][1] == fg and out[-1][2] == bg and out[-1][3] == bold:
                out[-1][0] += ch
            else:
                out.append([ch, fg, bg, bold])
        if out and out[-1][2] is None:
            out[-1][0] = out[-1][0].rstrip()
            if not out[-1][0]:
                out.pop()
        return out

    def text(self):
        """Plain text of scrollback + screen (for tests and copy)."""
        rows = self.history + self.grid
        return '\n'.join(''.join(c[0] for c in r).rstrip() for r in rows).rstrip()

    def snapshot(self, scroll=0):
        """Screen rows, or `scroll` rows back into the history."""
        scroll = max(0, min(scroll, len(self.history)))
        if scroll:
            rows = (self.history[len(self.history) - scroll:] + self.grid)[:self.rows]
        else:
            rows = self.grid
        return {'cols': self.cols, 'rows': [self.runs(r) for r in rows], 'cursor': [self.x, self.y],
                'cursor_visible': self.cursor_visible and not scroll, 'history': len(self.history),
                'alt': self.saved_main is not None,
                'scroll': scroll, 'title': self.title, 'version': self.version}
