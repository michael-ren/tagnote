command -nargs=* WriteAsDate :call WriteAsDate(<f-args>)

let NOTES_DIRECTORY = $HOME . '/notes'

let UTC = 0

function WriteAsDate(...)
  if g:UTC
    let filename = system('date -u +%F_%H-%M-%S.txt')
  else
    let filename = strftime('%F_%H-%M-%S.txt')
  endif
  let tags = ''
  for arg in a:000
    let check = system('tag -r :0 members' . ' ' . shellescape(l:arg))
    if v:shell_error
      echo check
      break
    endif
    let tags = l:tags . ' ' . shellescape(l:arg)
  endfor
  if !v:shell_error
    execute 'write ' . g:NOTES_DIRECTORY . '/' . l:filename
    let result = system('tag -r :0 add ' . l:filename . l:tags)
    if v:shell_error
      echo result
    endif
  endif
endfunction

