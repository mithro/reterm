#!/usr/bin/env python3
"""Create a transparent cursor theme for Wayland kiosk (no visible cursor).

Creates ~/.icons/transparent/cursors/ with a 1x1 transparent cursor for
all standard cursor names. Set XCURSOR_THEME=transparent to use.
"""
import os
import struct

# Xcursor file format:
# Header: magic (4), header_size (4), version (4), ntoc (4)
# TOC entries: type (4), subtype (4), position (4)
# Chunks: header_size (4), type (4), subtype (4), version (4)
#   For image: width (4), height (4), xhot (4), yhot (4), delay (4), pixels (w*h*4)

XCURSOR_MAGIC = 0x72756358  # "Xcur"
XCURSOR_IMAGE_TYPE = 0xfffd0002

def create_transparent_cursor():
    """Create a 1x1 transparent Xcursor file."""
    width = 1
    height = 1
    xhot = 0
    yhot = 0
    delay = 1
    # Single transparent pixel (ARGB = 0x00000000)
    pixel = b'\x00\x00\x00\x00'

    # Image chunk
    chunk_header_size = 36  # 9 * 4 bytes
    chunk = struct.pack('<IIIIIIIII',
        chunk_header_size,      # header size
        XCURSOR_IMAGE_TYPE,     # type
        width,                  # subtype (nominal size)
        1,                      # version
        width,                  # width
        height,                 # height
        xhot,                   # xhot
        yhot,                   # yhot
        delay,                  # delay
    ) + pixel

    # TOC entry
    toc_entry = struct.pack('<III',
        XCURSOR_IMAGE_TYPE,     # type
        width,                  # subtype (nominal size)
        16 + 12,                # position (header + toc)
    )

    # File header
    header = struct.pack('<IIII',
        XCURSOR_MAGIC,          # magic
        16,                     # header size
        0x00010000,             # version 1.0
        1,                      # number of TOC entries
    )

    return header + toc_entry + chunk


# Standard cursor names that need transparent versions
CURSOR_NAMES = [
    'default', 'left_ptr', 'arrow', 'top_left_arrow',
    'pointer', 'hand', 'hand1', 'hand2', 'grab', 'grabbing',
    'text', 'xterm', 'ibeam',
    'crosshair', 'cross', 'tcross',
    'wait', 'watch', 'progress', 'left_ptr_watch',
    'move', 'fleur', 'all-scroll',
    'not-allowed', 'forbidden', 'no-drop', 'circle',
    'n-resize', 's-resize', 'e-resize', 'w-resize',
    'ne-resize', 'nw-resize', 'se-resize', 'sw-resize',
    'ns-resize', 'ew-resize', 'nesw-resize', 'nwse-resize',
    'col-resize', 'row-resize',
    'top_side', 'bottom_side', 'left_side', 'right_side',
    'top_left_corner', 'top_right_corner',
    'bottom_left_corner', 'bottom_right_corner',
    'context-menu', 'help', 'question_arrow',
    'copy', 'alias', 'dnd-move', 'dnd-copy', 'dnd-link', 'dnd-none',
    'link', 'pirate', 'sb_h_double_arrow', 'sb_v_double_arrow',
    'size_bdiag', 'size_fdiag', 'size_hor', 'size_ver', 'size_all',
    'split_h', 'split_v', 'zoom-in', 'zoom-out',
    'X_cursor', 'based_arrow_down', 'based_arrow_up',
]

def main():
    cursor_dir = os.path.expanduser('~/.icons/transparent/cursors')
    os.makedirs(cursor_dir, exist_ok=True)

    cursor_data = create_transparent_cursor()

    # Write the cursor theme index
    index_path = os.path.expanduser('~/.icons/transparent/index.theme')
    with open(index_path, 'w') as f:
        f.write('[Icon Theme]\nName=transparent\nComment=Transparent cursor for kiosk\n')

    # Write cursor files
    for name in CURSOR_NAMES:
        path = os.path.join(cursor_dir, name)
        with open(path, 'wb') as f:
            f.write(cursor_data)

    print(f'Created transparent cursor theme with {len(CURSOR_NAMES)} cursors')
    print(f'Theme directory: {cursor_dir}')
    print('Set XCURSOR_THEME=transparent to use')


if __name__ == '__main__':
    main()
