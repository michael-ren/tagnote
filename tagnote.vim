" Copyright 2019 Michael Ren <michael.ren@mailbox.org>
"
" This program is free software: you can redistribute it and/or modify
" it under the terms of the GNU General Public License as published by
" the Free Software Foundation, either version 3 of the License, or
" (at your option) any later version.
"
" This program is distributed in the hope that it will be useful,
" but WITHOUT ANY WARRANTY; without even the implied warranty of
" MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
" GNU General Public License for more details.
"
" You should have received a copy of the GNU General Public License
" along with this program.  If not, see <https://www.gnu.org/licenses/>.


" tagnote.vim: Vim plugin to create and organize notes with tagnote

command -nargs=* WriteAsDate :call WriteAsDate(<f-args>)
command -nargs=* AddTagAsDate :call AddTagAsDate(<f-args>)

let TAGNOTE_NOTES_DIRECTORY = simplify($HOME . '/notes')

let TAGNOTE_UTC = 0

let TAGNOTE_NOTE_REGEX = '[0-9]\{4}-[0-9]\{2}-[0-9]\{2}_[0-9]\{2}-[0-9]\{2}-[0-9]\{2}.txt'


function s:run_with_error_output(command, error)
  let result = system(a:command)
  if v:shell_error
    echo a:error . l:result
  endif
  return v:shell_error
endfunction

function s:is_note(directory, file)
  return a:directory == g:TAGNOTE_NOTES_DIRECTORY
      \ && a:file =~ '^' . g:TAGNOTE_NOTE_REGEX . '$'
endfunction

function WriteAsDate(...)
  if g:TAGNOTE_UTC
    let filename = system('date -u +%F_%H-%M-%S.txt')
    if v:shell_error
      echo "Could not get timestamp\n\n" . l:filename
      return v:shell_error
    endif
  else
    let filename = strftime('%F_%H-%M-%S.txt')
  endif

  let old_directory = simplify(expand('%:p:h'))
  let old_file = expand('%:t')
  if s:is_note(l:old_directory, l:old_file)
    let prototype = '-p ' . shellescape(l:old_file)
  else
    let prototype = ''
  endif

  let tags = ''
  for arg in a:000
    let tags = l:tags . ' ' . shellescape(l:arg)
  endfor

  execute 'saveas ' . g:TAGNOTE_NOTES_DIRECTORY . '/' . l:filename
  return s:run_with_error_output(
    \'tag -r :0 add ' . l:prototype . ' ' . l:filename . l:tags,
    \"Could not add note\n\n"
    \)
endfunction

function AddTagAsDate(...)
  let tags = ''
  for arg in a:000
    let tags = l:tags . ' ' . shellescape(l:arg)
  endfor

  let directory = simplify(expand('%:p:h'))
  let file = expand('%:t')
  if s:is_note(l:directory, l:file)
    return s:run_with_error_output(
      \'tag -r :0 add ' . l:file . ' ' . l:tags,
      \"Could not add tag to note\n\n"
      \)
  else
    echo "File is not a note; run :W first\n"
    return 1
  endif
endfunction
