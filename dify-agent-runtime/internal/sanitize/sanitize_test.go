package sanitize

import (
	"testing"
)

func TestPlainText(t *testing.T) {
	s := New()
	out := s.Feed([]byte("hello\nworld\n"))
	out = append(out, s.Flush()...)
	expected := "hello\nworld\n"
	if string(out) != expected {
		t.Errorf("got %q, want %q", string(out), expected)
	}
}

func TestStripCSI(t *testing.T) {
	// ESC [ 31 m = red color, ESC [ 0 m = reset
	input := []byte("\x1b[31mred\x1b[0m\n")
	s := New()
	out := s.Feed(input)
	out = append(out, s.Flush()...)
	expected := "red\n"
	if string(out) != expected {
		t.Errorf("got %q, want %q", string(out), expected)
	}
}

func TestCarriageReturnOverwrite(t *testing.T) {
	// Progress: "50%" CR "100%" LF -> only "100%" visible
	input := []byte("50%\r100%\n")
	s := New()
	out := s.Feed(input)
	out = append(out, s.Flush()...)
	expected := "100%\n"
	if string(out) != expected {
		t.Errorf("got %q, want %q", string(out), expected)
	}
}

func TestCRLF(t *testing.T) {
	input := []byte("line1\r\nline2\r\n")
	s := New()
	out := s.Feed(input)
	out = append(out, s.Flush()...)
	expected := "line1\nline2\n"
	if string(out) != expected {
		t.Errorf("got %q, want %q", string(out), expected)
	}
}

func TestOSCSequence(t *testing.T) {
	// OSC: ESC ] ... BEL
	input := []byte("\x1b]0;title\x07visible\n")
	s := New()
	out := s.Feed(input)
	out = append(out, s.Flush()...)
	expected := "visible\n"
	if string(out) != expected {
		t.Errorf("got %q, want %q", string(out), expected)
	}
}

func TestFlushUnterminatedLine(t *testing.T) {
	s := New()
	out := s.Feed([]byte("no newline"))
	out = append(out, s.Flush()...)
	expected := "no newline"
	if string(out) != expected {
		t.Errorf("got %q, want %q", string(out), expected)
	}
}

func TestInvalidUTF8(t *testing.T) {
	// 0xFF is not valid UTF-8, should be replaced
	s := New()
	out := s.Feed([]byte{0xFF, 'a', '\n'})
	out = append(out, s.Flush()...)
	expected := "\uFFFDa\n"
	if string(out) != expected {
		t.Errorf("got %q, want %q", string(out), expected)
	}
}

func TestUTF8AcrossEveryByteBoundary(t *testing.T) {
	input := []byte("技能：不给危险操作步骤。🙂\r\n\x1b[31m红色\x1b[0m\n")
	for split := 0; split <= len(input); split++ {
		s := New()
		out := s.Feed(input[:split])
		out = append(out, s.Feed(input[split:])...)
		out = append(out, s.Flush()...)
		if string(out) != "技能：不给危险操作步骤。🙂\n红色\n" {
			t.Fatalf("split %d corrupted UTF-8: %q", split, out)
		}
	}
}

func TestUTF8ByteAtATimeAndTruncatedEnd(t *testing.T) {
	s := New()
	var out []byte
	for _, b := range []byte("中文🙂") {
		out = append(out, s.Feed([]byte{b})...)
	}
	out = append(out, s.Feed([]byte{0xe4, 0xb8})...)
	out = append(out, s.Flush()...)
	if string(out) != "中文🙂\uFFFD" {
		t.Fatalf("got %q", out)
	}
	if len(s.Flush()) != 0 {
		t.Fatal("second flush must not repeat the incomplete rune")
	}
}
